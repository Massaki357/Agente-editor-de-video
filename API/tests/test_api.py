"""API FastAPI: projetos, clipes, jobs e arquivos (LLM sempre bloqueado; Whisper falso)."""

import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.api.tasks as tasks
import src.pipeline as pipeline
from src.api.app import create_app
from src.api.jobs import Job, JobManager, JobStatus
from src.cache import file_hash, write_json_cache
from src.config import PROJECT_ROOT, get_settings
from src.transcribe import Palavra, Transcricao, cache_key
from tests.conftest import make_video


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def novo_projeto(client, nome="teste") -> str:
    r = client.post("/api/projects", json={"nome": nome})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_history_empty_project_cannot_undo(client):
    pid = novo_projeto(client)
    history = client.get(f"/api/projects/{pid}/history")
    assert history.status_code == 200
    assert history.json() == {
        "entries": [], "cursor": -1, "can_undo": False, "can_redo": False,
    }
    assert client.post(f"/api/projects/{pid}/history/undo").status_code == 409


def test_chat_requires_rendered_video_and_nonempty_message(client):
    pid = novo_projeto(client)
    chat = client.get(f"/api/projects/{pid}/chat")
    assert chat.status_code == 200
    assert chat.json() == {"messages": [], "pending_token": None}
    assert client.post(f"/api/projects/{pid}/chat", json={"message": " "}).status_code == 422
    assert client.post(
        f"/api/projects/{pid}/chat", json={"message": "tira o zoom"}
    ).status_code == 409


def esperar(client, jid: str, timeout: float = 60) -> dict:
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        job = client.get(f"/api/jobs/{jid}").json()
        if job["status"] in ("concluido", "erro", "cancelado"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {jid} não terminou: {job}")


def tom(path: Path) -> Path:
    """4 s: tom em 1–2 s e 2,5–3,5 s, silêncio no resto."""
    expr = "0.5*sin(2*PI*440*t)*(between(t,1,2)+between(t,2.5,3.5))"
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    cmd += ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=4"]
    cmd += ["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d=4"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    subprocess.run([*cmd, "-shortest", str(path)], check=True, capture_output=True)
    return path


def fake_whisper(monkeypatch):
    """Transcritor falso que também grava no cache (como o real)."""
    chamadas: list[Path] = []

    def transcribe(path, settings=None, use_cache=True, on_progress=None):
        path = Path(path)
        chamadas.append(path)
        settings = settings or get_settings()
        t = Transcricao(
            arquivo_hash=file_hash(path),
            modelo="fake",
            duracao=4.0,
            palavras=[
                Palavra(indice=0, texto="um", inicio=1.0, fim=2.0),
                Palavra(indice=1, texto="dois", inicio=2.5, fim=3.5),
            ],
        )
        write_json_cache(
            "transcricao", cache_key(t.arquivo_hash, settings), t.model_dump(), settings.cache_dir
        )
        return t

    monkeypatch.setattr(pipeline, "transcribe_clip", transcribe)
    return chamadas


# ------------------------------------------------------------------ sistema


def test_health_config_and_doctor(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    config = client.get("/api/config")
    assert config.status_code == 200
    assert "api_key" not in config.text.lower()  # nunca expõe segredos
    assert config.json()["saida"] == {"largura": 1080, "altura": 1920, "fps": 30}
    corpo = config.json()
    assert corpo["llm_model"] in corpo["llm_models"]  # o modelo do .env sempre é escolhível
    assert corpo["opcoes_padrao"]["llm_model"] is None
    # a limpeza de áudio (novas-etapas, Parte 1) chega ao frontend desligada
    assert corpo["opcoes_padrao"]["limpar_audio"] is False
    assert corpo["opcoes_padrao"]["parametros_audio"]["aggressiveness"] == 0.5
    assert corpo["opcoes_padrao"]["estabilizar"] is False
    assert corpo["opcoes_padrao"]["suavizacao_estabilizacao"] == "medio"
    assert corpo["opcoes_padrao"]["broll"] is False
    assert corpo["opcoes_padrao"]["broll_intervalo_min"] == 8.0
    assert corpo["opcoes_padrao"]["broll_transition"] == "hard_cut"
    doctor = client.get("/api/doctor").json()
    assert {"nome", "status", "detalhe"} <= set(doctor[0])
    assert any(c["nome"] == "ffmpeg" for c in doctor)


def test_stabilization_options_are_saved_in_project_json_and_reused(client, video_dir, monkeypatch):
    from datetime import UTC, datetime

    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(video_dir)})
    captured = []

    def submit(self, projeto_id, tipo, opcoes=None):
        captured.append(opcoes)
        return Job(
            id=f"fake{len(captured)}",
            projeto_id=projeto_id,
            tipo=tipo,
            opcoes=opcoes or {},
            criado=datetime.now(UTC),
        )

    monkeypatch.setattr(JobManager, "submit", submit)
    r = client.post(
        f"/api/projects/{pid}/jobs",
        json={
            "tipo": "rosto",
            "opcoes": {"estabilizar": True, "suavizacao_estabilizacao": "forte"},
        },
    )
    assert r.status_code == 202, r.text
    project = client.get(f"/api/projects/{pid}").json()
    assert project["estabilizar"] is True
    assert project["suavizacao_estabilizacao"] == "forte"
    assert captured[0]["estabilizar"] is True

    r = client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar"})
    assert r.status_code == 202, r.text
    assert {key: captured[1][key] for key in (
        "estabilizar", "suavizacao_estabilizacao", "broll", "broll_intervalo_min",
        "broll_transition"
    )} == {
        "estabilizar": True,
        "suavizacao_estabilizacao": "forte",
        "broll": False,
        "broll_intervalo_min": 8.0,
        "broll_transition": "hard_cut",
    }
    assert captured[1]["legendas_continuas"] is True
    assert captured[1]["legendas_destaque"] is False
    path = get_settings().data_dir / "projects" / pid / "project.json"
    assert '"estabilizar": true' in path.read_text(encoding="utf-8")

    invalido = client.post(
        f"/api/projects/{pid}/jobs",
        json={
            "tipo": "gerar",
            "opcoes": {"legendas_continuas": True, "legendas_destaque": True},
        },
    )
    assert invalido.status_code == 422
    assert "nunca as duas" in invalido.text

    destaque = client.post(
        f"/api/projects/{pid}/jobs",
        json={
            "tipo": "gerar",
            "opcoes": {"legendas_continuas": False, "legendas_destaque": True},
        },
    )
    assert destaque.status_code == 202
    salvo = client.get(f"/api/projects/{pid}").json()
    assert salvo["legendas_continuas"] is False
    assert salvo["legendas_destaque"] is True
    assert client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar"}).status_code == 202
    assert captured[-1]["legendas_destaque"] is True


def test_broll_options_are_saved_and_reused(client, video_dir, monkeypatch):
    from datetime import UTC, datetime

    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(video_dir)})
    captured = []

    def submit(self, projeto_id, tipo, opcoes=None):
        captured.append(opcoes)
        return Job(
            id=f"fake{len(captured)}",
            projeto_id=projeto_id,
            tipo=tipo,
            opcoes=opcoes or {},
            criado=datetime.now(UTC),
        )

    monkeypatch.setattr(JobManager, "submit", submit)
    options = {"broll": True, "broll_intervalo_min": 12, "broll_transition": "wipe"}
    r = client.post(f"/api/projects/{pid}/jobs", json={"tipo": "broll", "opcoes": options})
    assert r.status_code == 202, r.text
    project = client.get(f"/api/projects/{pid}").json()
    assert {key: project[key] for key in options} == options
    assert {key: captured[0][key] for key in options} == options
    assert client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar"}).status_code == 202
    assert {key: captured[1][key] for key in options} == options
    saved = get_settings().data_dir / "projects" / pid / "project.json"
    assert '"broll_transition": "wipe"' in saved.read_text(encoding="utf-8")
    for invalid in (-1, 7.99, 30.01):
        r = client.post(
            f"/api/projects/{pid}/jobs",
            json={"tipo": "broll", "opcoes": {"broll_intervalo_min": invalid}},
        )
        assert r.status_code == 422


# ------------------------------------------------------------------ projetos


def test_project_crud(client):
    pid = novo_projeto(client, "Meu vídeo")
    assert client.get("/api/projects").json()[0]["id"] == pid
    r = client.patch(f"/api/projects/{pid}", json={"nome": "Outro nome"})
    assert r.json()["nome"] == "Outro nome"
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404


@pytest.mark.parametrize("pid", ["naoexiste", "..%2F..%2Fetc"])
def test_unknown_or_malicious_project_id_is_404(client, pid):
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_import_folder_in_natural_order(client, video_dir):
    pid = novo_projeto(client)
    r = client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(video_dir)})
    assert r.status_code == 200, r.text
    clipes = r.json()["clipes"]
    assert [c["nome"] for c in clipes] == ["1.mp4", "2.mp4", "10.mp4"]
    assert clipes[1]["offset"] == pytest.approx(clipes[0]["duracao_mantida"])
    assert clipes[2]["tem_audio"] is False
    assert (clipes[0]["largura"], clipes[0]["altura"]) == (320, 180)
    # Etapa 11: a interface precisa dos avisos de fonte difícil
    assert all(c["vfr"] is False and c["hdr"] is False for c in clipes)


def test_import_invalid_folder(client, tmp_path):
    pid = novo_projeto(client)
    r = client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(tmp_path / "x")})
    assert r.status_code == 422
    r = client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(tmp_path)})
    assert r.status_code == 422 and "nenhum vídeo" in r.json()["detail"]


def test_upload_keeps_order_and_rejects_bad_files(client, tmp_path):
    pid = novo_projeto(client)
    a, b = make_video(tmp_path / "b.mp4"), make_video(tmp_path / "a.mp4", duration=0.5)
    files = [
        ("files", ("b.mp4", a.read_bytes(), "video/mp4")),
        ("files", ("a.mp4", b.read_bytes(), "video/mp4")),
    ]
    r = client.post(f"/api/projects/{pid}/clips", files=files)
    assert [c["nome"] for c in r.json()["clipes"]] == ["b.mp4", "a.mp4"]  # ordem de envio

    r = client.post(f"/api/projects/{pid}/clips", files=[("files", ("x.txt", b"oi", "text/plain"))])
    assert r.status_code == 422
    r = client.post(
        f"/api/projects/{pid}/clips", files=[("files", ("q.mp4", b"lixo", "video/mp4"))]
    )
    assert r.status_code == 422
    clips_dir = get_settings().data_dir / "projects" / pid / "clips"
    assert sorted(p.name for p in clips_dir.iterdir()) == ["a.mp4", "b.mp4"]  # lixo apagado


def test_reorder_and_remove(client, video_dir, tmp_path):
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(video_dir)})
    r = client.put(f"/api/projects/{pid}/clips/order", json={"ordem": [2, 0, 1]})
    clipes = r.json()["clipes"]
    assert [c["nome"] for c in clipes] == ["10.mp4", "1.mp4", "2.mp4"]
    assert clipes[0]["offset"] == 0 and clipes[1]["offset"] == pytest.approx(0.5, abs=0.05)
    assert (
        client.put(f"/api/projects/{pid}/clips/order", json={"ordem": [0, 0, 1]}).status_code == 422
    )

    r = client.delete(f"/api/projects/{pid}/clips/0")
    assert [c["nome"] for c in r.json()["clipes"]] == ["1.mp4", "2.mp4"]
    assert (video_dir / "10.mp4").exists()  # importado de fora: o arquivo fica
    assert client.delete(f"/api/projects/{pid}/clips/9").status_code == 404


def test_clip_video_thumbnail_and_range(client, video_dir):
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(video_dir)})
    video = client.get(f"/api/projects/{pid}/clips/0/video")
    assert video.status_code == 200 and len(video.content) > 1000
    parcial = client.get(f"/api/projects/{pid}/clips/0/video", headers={"Range": "bytes=0-99"})
    assert parcial.status_code == 206 and len(parcial.content) == 100
    thumb = client.get(f"/api/projects/{pid}/clips/0/thumbnail")
    assert thumb.status_code == 200 and thumb.content[:2] == b"\xff\xd8"  # JPEG
    assert client.get(f"/api/projects/{pid}/clips/0/transcricao").status_code == 404
    assert client.get(f"/api/projects/{pid}/files/../info.json").status_code == 404


# ------------------------------------------------------------------ jobs


def test_transcribe_job_then_transcription_endpoint(client, tmp_path, monkeypatch):
    chamadas = fake_whisper(monkeypatch)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    job = client.post(f"/api/projects/{pid}/jobs", json={"tipo": "transcrever"})
    assert job.status_code == 202
    job = esperar(client, job.json()["id"])
    assert job["status"] == "concluido", job
    assert job["resultado"]["clipes"][0]["palavras"] == 2
    assert len(chamadas) == 1
    t = client.get(f"/api/projects/{pid}/clips/0/transcricao").json()
    assert t["texto"] == "um dois" and t["palavras"][1]["inicio"] == 2.5
    assert client.get(f"/api/projects/{pid}").json()["clipes"][0]["transcrito"] is True


def _pasta_tom(tmp_path: Path) -> Path:
    pasta = tmp_path / "tom"
    pasta.mkdir()
    tom(pasta / "1.mp4")
    return pasta


def test_generate_job_cuts_and_renders(client, tmp_path, monkeypatch):
    fake_whisper(monkeypatch)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    r = client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar"})
    job = esperar(client, r.json()["id"])
    assert job["status"] == "concluido", job
    assert job["resultado"]["duracao_final"] < job["resultado"]["duracao_original"]
    assert any("LLM indisponível" in linha for linha in job["log"])  # LLM bloqueado → Etapa 3

    projeto = client.get(f"/api/projects/{pid}").json()
    assert len(projeto["clipes"][0]["trechos"]) == 2  # a pausa entre os tons foi cortada
    assert projeto["video_final_url"].startswith(f"/api/projects/{pid}/files/final.mp4")
    assert "final.mp4" in projeto["arquivos"]
    video = client.get(f"/api/projects/{pid}/files/final.mp4")
    assert video.status_code == 200 and len(video.content) > 1000
    from src.clips import probe_clip

    final = get_settings().data_dir / "projects" / pid / "saida" / "final.mp4"
    meta = probe_clip(final)
    assert (meta.largura, meta.altura, meta.fps) == (1080, 1920, 30)  # Etapa 6: 9:16
    assert "rosto" in job["resultado"]["tempos"]


def test_generate_without_cuts_keeps_whole_clip(client, tmp_path, monkeypatch):
    fake_whisper(monkeypatch)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    r = client.post(
        f"/api/projects/{pid}/jobs",
        json={"tipo": "gerar", "opcoes": {"cortes": False, "reenquadrar": False}},
    )
    job = esperar(client, r.json()["id"])
    assert job["status"] == "concluido", job
    assert job["resultado"]["duracao_final"] == pytest.approx(4.0, abs=0.05)
    from src.clips import probe_clip

    final = get_settings().data_dir / "projects" / pid / "saida" / "final.mp4"
    assert (probe_clip(final).largura, probe_clip(final).altura) == (320, 180)  # quadro original
    assert job["resultado"]["segmentos_renderizados"] > 0

    # A segunda geração com as mesmas decisões usa os MOVs já prontos.
    again = client.post(
        f"/api/projects/{pid}/jobs",
        json={"tipo": "gerar", "opcoes": {"cortes": False, "reenquadrar": False}},
    )
    reused = esperar(client, again.json()["id"])
    assert reused["status"] == "concluido", reused
    assert reused["resultado"]["segmentos_renderizados"] == 0
    assert reused["resultado"]["segmentos_reutilizados"] > 0


def test_running_job_blocks_changes_and_can_be_cancelled(client, video_dir, monkeypatch):
    def lento(store, job, ctx):
        for k in range(200):
            ctx.step("lento", k / 200)
            time.sleep(0.02)
        return {}

    monkeypatch.setattr(tasks, "gerar", lento)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(video_dir)})
    jid = client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar"}).json()["id"]
    time.sleep(0.2)
    assert client.get(f"/api/projects/{pid}").json()["job_ativo"] == jid
    assert (
        client.put(f"/api/projects/{pid}/clips/order", json={"ordem": [1, 0, 2]}).status_code == 409
    )
    assert client.post(f"/api/projects/{pid}/jobs", json={"tipo": "rosto"}).status_code == 409
    assert client.delete(f"/api/projects/{pid}").status_code == 409

    client.post(f"/api/jobs/{jid}/cancel")
    job = esperar(client, jid, timeout=10)
    assert job["status"] == "cancelado"
    assert client.get(f"/api/projects/{pid}").json()["job_ativo"] is None


def test_failing_job_reports_error(client, video_dir, monkeypatch):
    def quebra(store, job, ctx):
        raise RuntimeError("deu ruim")

    monkeypatch.setattr(tasks, "gerar", quebra)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(video_dir)})
    job = esperar(
        client, client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar"}).json()["id"]
    )
    assert job["status"] == "erro" and job["mensagem"] == "deu ruim"
    assert any("RuntimeError" in linha for linha in job["log"])
    assert client.get(f"/api/jobs?projeto_id={pid}").json()[0]["id"] == job["id"]


def test_job_needs_clips_and_valid_type(client):
    pid = novo_projeto(client)
    assert client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar"}).status_code == 422
    assert client.post(f"/api/projects/{pid}/jobs", json={"tipo": "xyz"}).status_code == 422
    assert client.get("/api/jobs/naoexiste").status_code == 404


def test_face_job_generates_debug_video(client, video_dir):
    asset = PROJECT_ROOT / ".cache" / "test-assets" / "blaze_face_short_range.tflite"
    if not asset.exists():
        pytest.skip("modelo de rosto não baixado (rode tests/test_face.py)")
    modelo = get_settings().cache_dir / "models" / asset.name
    modelo.parent.mkdir(parents=True, exist_ok=True)
    modelo.write_bytes(asset.read_bytes())

    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(video_dir)})
    job = esperar(
        client, client.post(f"/api/projects/{pid}/jobs", json={"tipo": "rosto"}).json()["id"]
    )
    assert job["status"] == "concluido", job
    nomes = [c["debug"] for c in job["resultado"]["clipes"]]
    assert len(set(nomes)) == 3 and all(n.startswith("rosto_") for n in nomes)
    # os vídeos sintéticos não têm rosto: o job avisa em vez de falhar (Etapa 11)
    avisos = job["resultado"]["avisos"]
    assert sum("rosto detectado em só 0%" in a for a in avisos) == 3
    assert sum("sem áudio" in a for a in avisos) == 1  # 10.mp4
    # reordenar não troca o vídeo de debug de clipe (nome pelo conteúdo, não pela posição)
    antes = client.get(f"/api/projects/{pid}/clips/0/rosto").json()["debug_url"]
    client.put(f"/api/projects/{pid}/clips/order", json={"ordem": [1, 0, 2]})
    assert client.get(f"/api/projects/{pid}/clips/1/rosto").json()["debug_url"] == antes
    rosto = client.get(f"/api/projects/{pid}/clips/0/rosto").json()
    assert rosto["cobertura"] == 0 and rosto["debug_url"].endswith(nomes[1])
    assert client.get(rosto["debug_url"]).status_code == 200


# ------------------------------------------------------------------ fila


def test_jobs_interrupted_by_restart_become_errors(tmp_path):
    manager = JobManager(tmp_path, lambda job, ctx: {})
    job = manager.submit("p1", "gerar")  # nunca começa: o worker não foi iniciado
    assert job.status == JobStatus.pendente
    reaberto = JobManager(tmp_path, lambda job, ctx: {})
    antigo = reaberto.get(job.id)
    assert antigo.status == JobStatus.erro and "reiniciada" in antigo.mensagem


def test_pending_job_can_be_cancelled_before_start(tmp_path):
    manager = JobManager(tmp_path, lambda job, ctx: {})
    job: Job = manager.submit("p1", "gerar")
    assert manager.cancel(job.id).status == JobStatus.cancelado


def test_clip_urls_change_when_clip_at_position_changes(client, video_dir):
    pid = novo_projeto(client)
    antes = client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(video_dir)}).json()
    depois = client.put(f"/api/projects/{pid}/clips/order", json={"ordem": [1, 0, 2]}).json()
    assert antes["clipes"][0]["video_url"] != depois["clipes"][0]["video_url"]
    versao = lambda url: url.split("?v=")[1]  # noqa: E731
    assert versao(antes["clipes"][0]["thumbnail_url"]) == versao(
        depois["clipes"][1]["thumbnail_url"]
    )
    assert client.get(depois["clipes"][0]["thumbnail_url"]).status_code == 200


def test_failed_upload_leaves_no_orphan_files(client, tmp_path):
    pid = novo_projeto(client)
    bom = make_video(tmp_path / "bom.mp4")
    files = [
        ("files", ("bom.mp4", bom.read_bytes(), "video/mp4")),
        ("files", ("ruim.mp4", b"lixo", "video/mp4")),
    ]
    r = client.post(f"/api/projects/{pid}/clips", files=files)
    assert r.status_code == 422
    assert list((get_settings().data_dir / "projects" / pid / "clips").iterdir()) == []
    assert client.get(f"/api/projects/{pid}").json()["clipes"] == []
    r = client.post(f"/api/projects/{pid}/clips", files=[("files", ("x.avi", b"x", "video/x"))])
    assert "use .mkv, .mov, .mp4" in r.json()["detail"]


def test_relative_import_path_is_from_repo_root(client, monkeypatch, video_dir):
    import src.api.routes.projects as rotas

    monkeypatch.setattr(rotas, "REPO_ROOT", video_dir.parent)
    pid = novo_projeto(client)
    r = client.post(f"/api/projects/{pid}/clips/import", json={"pasta": video_dir.name})
    assert r.status_code == 200 and len(r.json()["clipes"]) == 3


def test_missing_clip_file_gives_404_not_500(client, tmp_path):
    pasta = tmp_path / "some"
    pasta.mkdir()
    make_video(pasta / "1.mp4")
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(pasta)})
    (pasta / "1.mp4").unlink()
    for rota in ("video", "thumbnail", "transcricao", "rosto"):
        assert client.get(f"/api/projects/{pid}/clips/0/{rota}").status_code == 404, rota
    assert client.get(f"/api/projects/{pid}").status_code == 200


def test_output_files_open_inline(client, tmp_path, monkeypatch):
    fake_whisper(monkeypatch)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    esperar(client, client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar"}).json()["id"])
    r = client.get(f"/api/projects/{pid}/files/final.mp4")
    assert r.headers["content-disposition"].startswith("inline")


def test_generate_with_custom_caption_style(client, tmp_path, monkeypatch):
    fake_whisper(monkeypatch)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    opcoes = {"estilo_legenda": {"cor_destaque": "#00FF00", "maiusculas": False}}
    r = client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": opcoes})
    job = esperar(client, r.json()["id"])
    assert job["status"] == "concluido", job
    assert "legendas" in job["resultado"]["tempos"]
    ass = (get_settings().data_dir / "projects" / pid / "saida" / "final.ass").read_text("utf-8")
    assert r"\c&H00FF00&" in ass and "um" in ass  # destaque verde, sem caixa alta


def test_invalid_caption_color_is_rejected(client, video_dir):
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(video_dir)})
    opcoes = {"estilo_legenda": {"cor": "amarelo"}}
    r = client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": opcoes})
    assert r.status_code == 422


# ------------------------------------------------------------------ Etapa 8: imagens


def test_broll_preview_approval_swap_and_removal_reuse_the_plan(client, tmp_path, monkeypatch):
    """A prévia permite revisar sem nova chamada ao LLM; remoção volta à câmera."""
    import src.api.routes.broll as broll_routes
    import src.broll.preview as broll_preview
    from src.broll.planner import ItemBroll
    from src.broll.source import PreparedBroll
    from src.config import Settings
    from src.images import ItemImagem, PlanoImagens, timeline_signature

    inputs = tmp_path / "inputs"
    inputs.mkdir()
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x288:r=30:d=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(inputs / "camera.mp4"),
        ],
        capture_output=True,
        check=True,
    )
    cache = tmp_path / "cache" / "broll_video"
    cache.mkdir(parents=True)
    blue = cache / "blue.mp4"
    green = cache / "green.mp4"
    for color, dest in (("blue", blue), ("green", green)):
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=160x288:r=30:d=1.5",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(dest),
            ],
            capture_output=True,
            check=True,
        )
    pid = novo_projeto(client)
    imported = client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(inputs)})
    assert imported.status_code == 200, imported.text
    fake_settings = Settings(cache_dir=tmp_path / "cache", output_width=160, output_height=288)
    monkeypatch.setattr(pipeline, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(broll_routes, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(pipeline, "face_tracks", lambda *a, **k: {})
    monkeypatch.setattr(pipeline, "reframe_cameras", lambda *a, **k: {})
    calls = {"plan": 0, "video": 0}

    def plan(project, previous, transcriber, params=None, settings=None):
        calls["plan"] += 1
        assert params.intervalo_min in (8.0, 12.0)
        assert previous.itens == []  # imagens desligadas não bloqueiam o planejamento
        updated = previous.model_copy(deep=True)
        updated.broll = [
            ItemBroll(
                id=0,
                clipe=0,
                trecho_inicio_palavra=0,
                trecho_fim_palavra=3,
                texto="colhemos café",
                query="coffee harvest",
                inicio=1,
                duracao_max=1.5,
                motivo="mostra colheita",
            )
        ]
        return updated

    def prepare(item, settings=None, params=None):
        calls["video"] += 1
        path = green if item.query == "green field" else blue
        return PreparedBroll(
            arquivo=path,
            query=item.query,
            duracao=1.5,
            fonte="pexels",
            id="green" if path == green else "blue",
            pagina="https://example.com/video",
        )

    monkeypatch.setattr(tasks, "plan_broll_project", plan)
    monkeypatch.setattr(broll_preview, "prepare_item", prepare)
    options = {
        "cortes": False,
        "imagens": False,
        "zooms": False,
        "legendas": False,
        "reenquadrar": True,
        "broll": True,
    }
    output = client.app.state.store.saida_dir(pid) / "final.mp4"

    def center_rgb():
        return tuple(
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-ss",
                    "1.7",
                    "-i",
                    str(output),
                    "-frames:v",
                    "1",
                    "-vf",
                    "crop=2:2:80:144",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-",
                ],
                capture_output=True,
                check=True,
            ).stdout[:3]
        )

    assert client.get(f"/api/projects/{pid}/broll").status_code == 404
    store = client.app.state.store
    store.save_plan(
        pid,
        PlanoImagens(
            assinatura=timeline_signature(store.load(pid)),
            itens=[
                ItemImagem(
                    id=0,
                    indice=0,
                    clipe=0,
                    palavra="café",
                    query="coffee",
                    inicio=1,
                    duracao=1.5,
                )
            ],
        ),
    )
    job = esperar(
        client,
        client.post(f"/api/projects/{pid}/jobs", json={"tipo": "broll", "opcoes": options}).json()[
            "id"
        ],
    )
    assert job["status"] == "concluido", job
    assert len(store.load_plan(pid).itens) == 1
    preview = client.get(f"/api/projects/{pid}/broll").json()
    assert preview["valido"] and preview["itens"][0]["texto"] == "colhemos café"
    assert preview["itens"][0]["duracao"] == 1.5
    assert preview["itens"][0]["aprovado"] is False
    assert client.get(preview["itens"][0]["video_url"]).status_code == 200
    assert client.patch(f"/api/projects/{pid}/broll/0", json={"aprovado": True}).status_code == 200

    first = esperar(
        client,
        client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": options}).json()[
            "id"
        ],
    )
    assert first["status"] == "concluido", first
    assert first["resultado"]["broll"] == 1
    assert center_rgb()[2] > 180  # azul sobre a câmera vermelha
    assert calls["plan"] == 1 and calls["video"] == 1

    disabled = client.patch(f"/api/projects/{pid}/broll/0", json={"ativo": False})
    assert disabled.status_code == 200 and disabled.json()["itens"][0]["aprovado"] is False
    second = esperar(
        client,
        client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": options}).json()[
            "id"
        ],
    )
    assert second["status"] == "concluido", second
    assert second["resultado"]["broll"] == 0
    assert center_rgb()[0] > 180  # a câmera voltou

    swapped = client.patch(
        f"/api/projects/{pid}/broll/0", json={"query": "green field", "ativo": True}
    )
    assert swapped.status_code == 200
    assert swapped.json()["itens"][0]["video_url"] is None
    assert client.patch(f"/api/projects/{pid}/broll/0", json={"aprovado": True}).status_code == 422
    job = esperar(
        client,
        client.post(f"/api/projects/{pid}/jobs", json={"tipo": "broll", "opcoes": options}).json()[
            "id"
        ],
    )
    assert job["status"] == "concluido", job
    assert calls["plan"] == 1 and calls["video"] == 2
    assert client.patch(f"/api/projects/{pid}/broll/0", json={"aprovado": True}).status_code == 200
    third = esperar(
        client,
        client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": options}).json()[
            "id"
        ],
    )
    assert third["status"] == "concluido", third
    assert third["resultado"]["broll"] == 1
    assert center_rgb()[1] > 80  # nova busca trocou o vídeo pelo verde
    assert calls["plan"] == 1 and calls["video"] == 2

    sparser = {**options, "broll_intervalo_min": 12}
    refreshed = esperar(
        client,
        client.post(f"/api/projects/{pid}/jobs", json={"tipo": "broll", "opcoes": sparser}).json()[
            "id"
        ],
    )
    assert refreshed["status"] == "concluido", refreshed
    assert calls["plan"] == 2
    assert client.get(f"/api/projects/{pid}/broll").json()["itens"][0]["aprovado"] is False

    def slow_job(store, job, ctx):
        for k in range(100):
            ctx.step("lento", k / 100)
            time.sleep(0.02)
        return {}

    monkeypatch.setattr(tasks, "gerar", slow_job)
    jid = client.post(
        f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": options}
    ).json()["id"]
    time.sleep(0.2)
    assert client.patch(f"/api/projects/{pid}/broll/0", json={"ativo": False}).status_code == 409
    client.post(f"/api/jobs/{jid}/cancel")
    esperar(client, jid)

    project = client.app.state.store.load(pid)
    project.timeline.substituir_trechos(0, [(0, 3)])
    client.app.state.store.save(pid, project)
    obsolete = client.get(f"/api/projects/{pid}/broll").json()
    assert obsolete["valido"] is False and obsolete["itens"][0]["video_url"] is None
    assert client.get(f"/api/projects/{pid}/broll/0/video").status_code == 409
    assert client.patch(f"/api/projects/{pid}/broll/0", json={"ativo": False}).status_code == 409


def _fake_images(monkeypatch, tmp_path, zooms=False):
    """LLM de imagens e busca falsos; conta as chamadas ao LLM."""
    from PIL import Image

    import src.images as images

    chamadas = {"llm": 0}

    def suggest(palavras, settings=None):
        chamadas["llm"] += 1
        if not palavras:
            return [], []
        zoom = [(1, palavras[1].palavra.texto, 1.5)] if zooms and len(palavras) > 1 else []
        return [(0, palavras[0].palavra.texto, "fruit basket", 2.0)], zoom

    def busca(query, n=5, settings=None):
        return [
            images.Candidato(fonte="pexels", id=f"{query}-{k}", url=f"https://x/{k}", miniatura="m")
            for k in range(3)
        ]

    foto = tmp_path / "foto.jpg"
    Image.new("RGB", (900, 600), (20, 200, 60)).save(foto)
    monkeypatch.setattr(images, "suggest", suggest)
    monkeypatch.setattr(images, "search_images", busca)
    monkeypatch.setattr("src.api.routes.images.search_images", busca)
    monkeypatch.setattr(images, "download", lambda url, s=None: foto)
    return chamadas


def test_image_preview_edit_and_render_without_calling_the_llm_again(client, tmp_path, monkeypatch):
    fake_whisper(monkeypatch)
    chamadas = _fake_images(monkeypatch, tmp_path)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    assert client.get(f"/api/projects/{pid}/imagens").status_code == 404

    job = esperar(
        client, client.post(f"/api/projects/{pid}/jobs", json={"tipo": "imagens"}).json()["id"]
    )
    assert job["status"] == "concluido", job
    assert job["resultado"] == {"itens": 1, "com_foto": 1, "zooms": 0}
    plano = client.get(f"/api/projects/{pid}/imagens").json()
    assert plano["valido"] is True
    (item,) = plano["plano"]["itens"]
    # "um" (1,0 s no original) cai em ~0,1 s no vídeo cortado; a imagem entra 0,2 s antes,
    # mas nunca antes do começo do clipe
    assert item["palavra"] == "um" and item["inicio"] == pytest.approx(0.0, abs=0.01)

    # trocar a foto, desativar/ativar e mudar a busca: nada disso chama o LLM
    r = client.patch(f"/api/projects/{pid}/imagens/{item['id']}", json={"escolhida": 2})
    assert r.json()["plano"]["itens"][0]["escolhida"] == 2
    assert (
        client.patch(f"/api/projects/{pid}/imagens/{item['id']}", json={"escolhida": 9}).status_code
        == 422
    )
    r = client.patch(f"/api/projects/{pid}/imagens/{item['id']}", json={"query": "green apple"})
    novo = r.json()["plano"]["itens"][0]
    assert novo["query"] == "green apple" and novo["escolhida"] == 0
    assert novo["candidatos"][0]["id"] == "green apple-0"
    assert chamadas["llm"] == 1

    job = esperar(
        client, client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar"}).json()["id"]
    )
    assert job["status"] == "concluido", job
    assert job["resultado"]["imagens"] == 1
    assert chamadas["llm"] == 1  # o render usou o plano salvo
    assert any("plano criativo salvo" in linha for linha in job["log"])


def test_image_plan_edit_is_blocked_during_a_job(client, tmp_path, monkeypatch):
    fake_whisper(monkeypatch)
    _fake_images(monkeypatch, tmp_path)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    esperar(client, client.post(f"/api/projects/{pid}/jobs", json={"tipo": "imagens"}).json()["id"])

    def lento(store, job, ctx):
        for k in range(100):
            ctx.step("lento", k / 100)
            time.sleep(0.02)
        return {}

    monkeypatch.setattr(tasks, "gerar", lento)
    jid = client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar"}).json()["id"]
    time.sleep(0.2)
    assert client.patch(f"/api/projects/{pid}/imagens/0", json={"ativa": False}).status_code == 409
    client.post(f"/api/jobs/{jid}/cancel")
    esperar(client, jid)
    assert client.patch(f"/api/projects/{pid}/imagens/0", json={"ativa": False}).status_code == 200
    assert client.patch(f"/api/projects/{pid}/imagens/7", json={"ativa": False}).status_code == 404


def test_new_query_keeps_an_item_the_user_turned_off(client, tmp_path, monkeypatch):
    fake_whisper(monkeypatch)
    _fake_images(monkeypatch, tmp_path)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    esperar(client, client.post(f"/api/projects/{pid}/jobs", json={"tipo": "imagens"}).json()["id"])
    client.patch(f"/api/projects/{pid}/imagens/0", json={"ativa": False})
    r = client.patch(f"/api/projects/{pid}/imagens/0", json={"query": "green apple"})
    assert r.json()["plano"]["itens"][0]["ativa"] is False


def test_zoom_plan_can_be_toggled_and_render_runs_with_zooms(client, tmp_path, monkeypatch):
    fake_whisper(monkeypatch)
    chamadas = _fake_images(monkeypatch, tmp_path, zooms=True)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    job = esperar(
        client, client.post(f"/api/projects/{pid}/jobs", json={"tipo": "imagens"}).json()["id"]
    )
    assert job["resultado"]["zooms"] == 1
    (zoom,) = client.get(f"/api/projects/{pid}/imagens").json()["plano"]["zooms"]
    assert zoom["ativo"] is True and 0.8 <= zoom["duracao"] <= 2.5

    r = client.patch(f"/api/projects/{pid}/imagens/zooms/{zoom['id']}", json={"ativo": False})
    assert r.status_code == 200 and r.json()["plano"]["zooms"][0]["ativo"] is False
    assert (
        client.patch(f"/api/projects/{pid}/imagens/zooms/9", json={"ativo": True}).status_code
        == 404
    )
    r = client.patch(f"/api/projects/{pid}/imagens/zooms/{zoom['id']}", json={"ativo": True})
    assert r.json()["plano"]["zooms"][0]["ativo"] is True

    job = esperar(
        client, client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar"}).json()["id"]
    )
    assert job["status"] == "concluido", job
    # sem rosto detectado, o zoom segue o enquadramento da câmera
    assert job["resultado"]["zooms"] == 1
    assert chamadas["llm"] == 1


def test_zoom_edit_is_blocked_during_a_job(client, tmp_path, monkeypatch):
    fake_whisper(monkeypatch)
    _fake_images(monkeypatch, tmp_path, zooms=True)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    esperar(client, client.post(f"/api/projects/{pid}/jobs", json={"tipo": "imagens"}).json()["id"])

    def lento(store, job, ctx):
        for k in range(100):
            ctx.step("lento", k / 100)
            time.sleep(0.02)
        return {}

    monkeypatch.setattr(tasks, "gerar", lento)
    jid = client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar"}).json()["id"]
    time.sleep(0.2)
    r = client.patch(f"/api/projects/{pid}/imagens/zooms/0", json={"ativo": False})
    assert r.status_code == 409
    client.post(f"/api/jobs/{jid}/cancel")
    esperar(client, jid)


def test_job_result_reports_llm_usage_and_cost(client, tmp_path, monkeypatch):
    """Etapa 11: tokens e custo estimado entram no resultado de qualquer job."""
    fake_whisper(monkeypatch)
    from src.api import tasks

    monkeypatch.setattr(
        tasks.client,
        "usage_totals",
        lambda: {"openai:gpt-5-mini": {"input_tokens": 2000, "output_tokens": 1000, "calls": 1}},
    )
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    job = esperar(
        client, client.post(f"/api/projects/{pid}/jobs", json={"tipo": "transcrever"}).json()["id"]
    )
    assert job["status"] == "concluido", job
    uso = job["resultado"]["llm"]
    assert uso["chamadas"] == 1 and uso["tokens_entrada"] == 2000 and uso["tokens_saida"] == 1000
    assert uso["custo_usd"] == pytest.approx(2000 * 0.25e-6 + 1000 * 2.0e-6)
    assert uso["modelos"] == ["openai:gpt-5-mini"]
    assert any("LLM: 1 chamada(s)" in linha for linha in job["log"])


def test_generate_job_with_clean_audio(client, tmp_path, monkeypatch):
    """A opção `limpar_audio` atravessa a API e chega ao render."""
    fake_whisper(monkeypatch)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    opcoes = {
        "limpar_audio": True,
        "parametros_audio": {"motor": "afftdn"},
        "cortes_fala": False,
        "legendas": False,
        "imagens": False,
        "zooms": False,
    }
    job = esperar(
        client,
        client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": opcoes}).json()[
            "id"
        ],
    )
    assert job["status"] == "concluido", job
    assert any("Áudio de" in linha for linha in job["log"])  # a cadeia de limpeza rodou
    assert client.get(f"/api/projects/{pid}").json()["video_final_url"]


def test_a_failed_audio_cleaning_only_warns(client, tmp_path, monkeypatch):
    """A limpeza falhou: o vídeo sai com o áudio original e o job avisa (Parte 1, Etapa 4)."""
    fake_whisper(monkeypatch)

    def falha(*a, **k):
        raise RuntimeError("highpass falhou em 1.mp4: disco cheio")

    monkeypatch.setattr(pipeline, "cached_audio", falha)
    pid = novo_projeto(client)
    client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(_pasta_tom(tmp_path))})
    opcoes = {"limpar_audio": True, "cortes_fala": False, "legendas": False, "imagens": False}
    job = esperar(
        client,
        client.post(f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": opcoes}).json()[
            "id"
        ],
    )
    assert job["status"] == "concluido", job  # o render não caiu junto
    avisos = job["resultado"]["avisos"]
    assert any("não deu para limpar o áudio" in a and "1.mp4" in a for a in avisos), avisos
    assert client.get(f"/api/projects/{pid}").json()["video_final_url"]
