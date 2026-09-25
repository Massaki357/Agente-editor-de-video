"""Parte 5, Etapa 2: troca por ID, alternativas e upload com render incremental."""

from __future__ import annotations

import io
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import src.editing.replace as replace_module
import src.pipeline as pipeline
from src.api.app import create_app
from src.api.store import ProjectStore
from src.broll.planner import ItemBroll, VideoBroll
from src.broll.source import PreparedBroll, VideoCandidate
from src.config import get_settings
from src.editing.replace import replace_element
from src.images import Candidato, ItemImagem, PlanoImagens, timeline_signature

from .conftest import requires_ffmpeg


def _candidate(tmp_path: Path, name: str, color: tuple[int, int, int]) -> Candidato:
    path = tmp_path / f"{name}.png"
    Image.new("RGB", (120, 80), color).save(path)
    return Candidato(
        fonte="upload", id=name, url=str(path), miniatura="/imagem",
        autor=name, pagina="",
    )


def _image_plan(tmp_path: Path) -> PlanoImagens:
    return PlanoImagens(
        assinatura="teste",
        itens=[ItemImagem(
            id=1, indice=1, clipe=0, palavra="fruta", query="fruit",
            inicio=0.5, duracao=1.0,
            candidatos=[
                _candidate(tmp_path, "vermelha", (255, 0, 0)),
                _candidate(tmp_path, "azul", (0, 0, 255)),
            ],
        )],
    )


def test_image_alternative_uses_saved_candidates_without_search(tmp_path, monkeypatch):
    plano = _image_plan(tmp_path)
    monkeypatch.setattr(replace_module, "search_images", lambda *_: pytest.fail("busca repetida"))
    chosen = replace_element(plano, "img_001", "alternativa", indice=1)
    assert chosen.itens[0].candidato.id == "azul"
    assert plano.itens[0].candidato.id == "vermelha"
    with pytest.raises(ValueError, match="índice"):
        replace_element(plano, "img_001", "alternativa", indice=9)
    monkeypatch.setattr(
        replace_module, "search_images",
        lambda query: [_candidate(tmp_path, query, (0, 255, 0))],
    )
    searched = replace_element(plano, "img_001", "busca", query="banana")
    assert searched.itens[0].query == "banana"
    assert searched.itens[0].candidato.id == "banana"


def test_broll_alternative_uses_saved_result_without_search(tmp_path, monkeypatch):
    candidate = VideoCandidate(
        fonte="pexels", id="7", url="https://example.com/video.mp4",
        pagina="https://example.com/7", autor="autor",
        largura=160, altura=288, duracao=3,
    )
    plano = PlanoImagens(
        assinatura="teste",
        broll=[ItemBroll(
            id=1, clipe=0, trecho_inicio_palavra=0, trecho_fim_palavra=1,
            texto="frase", query="fruit", inicio=1, duracao_max=2,
            motivo="teste", aprovado=True,
            alternativas=[candidate.model_dump(mode="json")],
        )],
    )
    monkeypatch.setattr(replace_module, "prepare_broll", lambda *_: pytest.fail("busca repetida"))
    called = []

    def prepare(found, query, duracao):
        called.append((found.id, query, duracao))
        return PreparedBroll(
            arquivo=tmp_path / "pronto.mp4", query=query, duracao=duracao,
            fonte=found.fonte, id=found.id, pagina=found.pagina, autor=found.autor,
        )

    monkeypatch.setattr(replace_module, "prepare_candidate", prepare)
    updated = replace_element(plano, "broll_001", "alternativa", indice=0)
    assert called == [("7", "fruit", 2)]
    assert updated.broll[0].video.id == "7"
    monkeypatch.setattr(
        replace_module, "prepare_broll",
        lambda query, duracao: PreparedBroll(
            arquivo=tmp_path / "novo.mp4", query=query, duracao=duracao,
            fonte="pexels", id="8", pagina="https://example.com/8",
            alternativas=[candidate],
        ),
    )
    searched = replace_element(plano, "broll_001", "busca", query="banana")
    assert searched.broll[0].query == "banana"
    assert searched.broll[0].video.id == "8"
    assert searched.broll[0].alternativas[0]["id"] == "7"


def _video(path: Path):
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=160x288:r=30:d=6",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        check=True, capture_output=True,
    )


def _color_video(path: Path, color: str, duration: int = 3):
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=160x288:r=30:d={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )


def _job(client: TestClient, jid: str):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = client.get(f"/api/jobs/{jid}").json()
        if result["status"] in {"concluido", "erro", "cancelado"}:
            return result
        time.sleep(0.1)
    raise AssertionError("job demorou demais")


def _frames(path: Path):
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return np.stack(frames)


@requires_ffmpeg
def test_image_upload_job_updates_only_its_segment(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "face_tracks", lambda *_: {})
    from src import images

    monkeypatch.setattr(images, "download", lambda url, settings=None: Path(url))
    clip = tmp_path / "camera.mp4"
    _video(clip)
    original = _candidate(tmp_path, "original", (255, 0, 0))
    with TestClient(create_app()) as client:
        pid = client.post("/api/projects", json={"nome": "troca"}).json()["id"]
        folder = tmp_path / "clips"
        folder.mkdir()
        source = folder / "camera.mp4"
        source.write_bytes(clip.read_bytes())
        assert client.post(
            f"/api/projects/{pid}/clips/import", json={"pasta": str(folder)}
        ).status_code == 200
        store = ProjectStore(get_settings().data_dir / "projects")
        project = store.load(pid)
        plan = PlanoImagens(
            assinatura=timeline_signature(project),
            itens=[ItemImagem(
                id=1, indice=0, clipe=0, palavra="fruta", query="fruit",
                inicio=0.5, duracao=1.0, candidatos=[original],
            )],
        )
        store.save_plan(pid, plan)
        options = {
            "cortes": False, "reenquadrar": False, "legendas_continuas": False,
            "imagens": True, "zooms": False,
        }
        first = client.post(
            f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": options}
        )
        before_job = _job(client, first.json()["id"])
        assert before_job["status"] == "concluido", before_job
        final = store.saida_dir(pid) / "final.mp4"
        before = _frames(final)
        old_project = store.load(pid)
        old_project.opcoes_ultima_geracao = {}  # projeto gerado antes desta etapa
        store.save(pid, old_project)
        assert client.get(f"/api/projects/{pid}").json()["pode_substituir_imagens"] is True

        monkeypatch.setattr(
            pipeline, "apply_project_cuts", lambda *_: pytest.fail("cortes refeitos")
        )
        rejected = client.post(
            f"/api/projects/{pid}/replace/img_001/upload",
            files={"file": ("corrompida.png", b"invalid image", "image/png")},
        )
        assert rejected.status_code == 422
        payload = io.BytesIO()
        Image.new("RGB", (120, 80), (0, 255, 0)).save(payload, format="PNG")
        upload = client.post(
            f"/api/projects/{pid}/replace/img_001/upload",
            files={"file": ("nova.png", payload.getvalue(), "image/png")},
        )
        assert upload.status_code == 202, upload.text
        replaced = _job(client, upload.json()["id"])
        assert replaced["status"] == "concluido", replaced
        assert replaced["resultado"]["segmentos_reutilizados"] > 0
        after = _frames(final)
        assert np.array_equal(before[120], after[120])
        assert not np.array_equal(before[30], after[30])
        assert store.load_plan(pid).itens[0].candidato.fonte == "upload"
        media = client.get(f"/api/projects/{pid}/replace/img_001/media")
        assert media.status_code == 200
        assert media.content == payload.getvalue()

        # Um resultado alternativo que falha ao preparar não é salvo como concluído.
        monkeypatch.setattr(
            images, "prepare_overlay", lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ValueError("imagem inválida")
            ),
        )
        failed = client.post(
            f"/api/projects/{pid}/replace/img_001",
            json={"modo": "alternativa", "indice": 0},
        )
        assert failed.status_code == 202
        assert _job(client, failed.json()["id"])["status"] == "erro"
        assert store.load_plan(pid).itens[0].candidato.fonte == "upload"
        assert np.array_equal(after, _frames(final))

        project = store.load(pid)
        project.opcoes_ultima_geracao["imagens"] = False
        store.save(pid, project)
        assert client.get(f"/api/projects/{pid}").json()["pode_substituir_imagens"] is False
        blocked = client.post(
            f"/api/projects/{pid}/replace/img_001",
            json={"modo": "alternativa", "indice": 0},
        )
        assert blocked.status_code == 409
        assert client.get(f"/api/projects/{pid}/replace/img_001/media").status_code == 200


@requires_ffmpeg
def test_broll_upload_job_replaces_cutaway_and_keeps_other_frames(tmp_path, monkeypatch):
    from src.broll import source as broll_source

    settings = get_settings().model_copy(update={"output_width": 160, "output_height": 288})
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(broll_source, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "face_tracks", lambda *_: {})
    clip = tmp_path / "camera.mp4"
    blue = tmp_path / "blue.mp4"
    green = tmp_path / "green.mp4"
    _video(clip)
    _color_video(blue, "blue")
    _color_video(green, "green")
    with TestClient(create_app()) as client:
        pid = client.post("/api/projects", json={"nome": "troca broll"}).json()["id"]
        folder = tmp_path / "clips"
        folder.mkdir()
        (folder / "camera.mp4").write_bytes(clip.read_bytes())
        assert client.post(
            f"/api/projects/{pid}/clips/import", json={"pasta": str(folder)}
        ).status_code == 200
        store = ProjectStore(settings.data_dir / "projects")
        project = store.load(pid)
        plan = PlanoImagens(
            assinatura=timeline_signature(project),
            broll=[ItemBroll(
                id=1, clipe=0, trecho_inicio_palavra=0, trecho_fim_palavra=1,
                texto="frase", query="blue", inicio=2, duracao_max=2,
                motivo="teste", aprovado=True,
                video=VideoBroll(
                    arquivo=blue, fonte="pexels", id="blue", pagina="https://example.com"
                ),
            )],
        )
        store.save_plan(pid, plan)
        options = {
            "cortes": False, "reenquadrar": True, "legendas_continuas": False,
            "imagens": False, "zooms": False, "broll": True,
        }
        first = client.post(
            f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": options}
        )
        initial = _job(client, first.json()["id"])
        assert initial["status"] == "concluido", initial
        final = store.saida_dir(pid) / "final.mp4"
        before = _frames(final)
        upload = client.post(
            f"/api/projects/{pid}/replace/broll_001/upload",
            files={"file": ("green.mp4", green.read_bytes(), "video/mp4")},
        )
        assert upload.status_code == 202, upload.text
        changed = _job(client, upload.json()["id"])
        assert changed["status"] == "concluido", changed
        assert changed["resultado"]["segmentos_reutilizados"] > 0
        after = _frames(final)
        assert np.array_equal(before[30], after[30])
        assert not np.array_equal(before[90], after[90])
        saved = store.load_plan(pid).broll[0]
        assert saved.video.fonte == "upload"
        assert client.get(f"/api/projects/{pid}/broll/1/video").status_code == 200
