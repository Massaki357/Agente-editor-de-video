"""Etapa 11: fluxo completo pela API, com vídeos reais (`pytest -m integration`).

Critério de aceite da etapa: do projeto vazio até o vídeo final sem intervenção
manual além de aprovar a prévia. Precisa de pelo menos 2 vídeos em `samples/`
(veja `testes-pendentes.md`); sem eles, os testes são pulados.
"""

import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.clips import list_clips_from_folder
from src.config import SAMPLES_DIR

pytestmark = pytest.mark.integration

TIMEOUT_JOB = 900  # s: transcrição na GPU + render de vários clipes


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """API de verdade (fila, jobs e pipeline), com DATA_DIR isolado."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from src.config import get_settings

    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


MAX_CLIPES = 3  # o critério de aceite da etapa fala em 3 clipes


def _clipes() -> list[Path]:
    # a mesma ordem natural da importação da API (1, 2, 10 — não 1, 10, 2)
    videos = list_clips_from_folder(SAMPLES_DIR)
    if len(videos) < 2:
        pytest.skip("coloque pelo menos 2 vídeos curtos em samples/ (veja testes-pendentes.md)")
    return videos[:MAX_CLIPES]


def _importar(client: TestClient, pid: str) -> list[dict]:
    """Importa `samples/` e deixa no máximo 3 clipes (o fluxo do critério de aceite)."""
    r = client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(SAMPLES_DIR)})
    assert r.status_code == 200, r.text
    clipes = r.json()["clipes"]
    while len(clipes) > MAX_CLIPES:
        clipes = client.delete(f"/api/projects/{pid}/clips/{len(clipes) - 1}").json()["clipes"]
    return clipes


def _esperar(client: TestClient, jid: str) -> dict:
    limite = time.time() + TIMEOUT_JOB
    while time.time() < limite:
        job = client.get(f"/api/jobs/{jid}").json()
        if job["status"] in ("concluido", "erro", "cancelado"):
            return job
        time.sleep(0.5)
    raise AssertionError(f"job {jid} não terminou em {TIMEOUT_JOB} s")


def _rodar(client: TestClient, pid: str, tipo: str, opcoes: dict | None = None) -> dict:
    r = client.post(f"/api/projects/{pid}/jobs", json={"tipo": tipo, "opcoes": opcoes})
    assert r.status_code == 202, r.text
    job = _esperar(client, r.json()["id"])
    assert job["status"] == "concluido", job.get("mensagem") or job
    return job


def _probe(video: Path) -> dict:
    saida = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(video)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(saida)


SEM_LLM = {"cortes": True, "cortes_fala": False, "imagens": False, "zooms": False}


def test_projeto_vazio_ate_video_final(client, tmp_path):
    """Criar → importar → transcrever → rosto → gerar, tudo pela API."""
    esperados = _clipes()
    pid = client.post("/api/projects", json={"nome": "Integração"}).json()["id"]
    clipes = _importar(client, pid)
    assert [c["nome"] for c in clipes] == [p.name for p in esperados]

    _rodar(client, pid, "transcrever")
    _rodar(client, pid, "rosto")
    job = _rodar(client, pid, "gerar", SEM_LLM)

    resultado = job["resultado"]
    assert resultado["duracao_final"] > 0
    assert resultado["duracao_final"] <= resultado["duracao_original"]
    assert isinstance(resultado.get("avisos", []), list)

    projeto = client.get(f"/api/projects/{pid}").json()
    assert projeto["video_final_url"]
    video = client.get(projeto["video_final_url"])
    assert video.status_code == 200 and len(video.content) > 100_000
    destino = tmp_path / "final.mp4"
    destino.write_bytes(video.content)

    streams = _probe(destino)["streams"]
    v = next(s for s in streams if s["codec_type"] == "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in streams)
    # vídeo e áudio com a mesma duração (sincronia não derivou ao longo das emendas)
    duracoes = [float(s["duration"]) for s in streams if s.get("duration")]
    assert max(duracoes) - min(duracoes) < 0.2


def test_previa_aprovada_e_reaproveitada_no_render(client):
    """Com LLM: a prévia é gerada, o usuário aprova/edita e o render não chama o LLM de novo."""
    _clipes()
    pid = client.post("/api/projects", json={"nome": "Integração LLM"}).json()["id"]
    _importar(client, pid)

    job = _rodar(client, pid, "imagens")
    assert "llm" in job["resultado"], "o job da prévia deveria ter chamado o LLM"
    uso = job["resultado"]["llm"]
    assert uso["chamadas"] >= 1 and uso["tokens_entrada"] > 0
    assert uso["custo_usd"] is None or uso["custo_usd"] >= 0

    plano = client.get(f"/api/projects/{pid}/imagens").json()
    assert plano["valido"] is True
    for zoom in plano["plano"]["zooms"][:1]:  # "aprovar": desliga o primeiro zoom
        r = client.patch(f"/api/projects/{pid}/imagens/zooms/{zoom['id']}", json={"ativo": False})
        assert r.status_code == 200

    job = _rodar(client, pid, "gerar")
    assert any("plano criativo salvo" in linha for linha in job["log"])
    ativos = sum(z["ativo"] for z in plano["plano"]["zooms"]) - 1
    assert job["resultado"]["zooms"] <= max(ativos, 0)  # o zoom desligado não entrou
    assert client.get(f"/api/projects/{pid}").json()["video_final_url"]
