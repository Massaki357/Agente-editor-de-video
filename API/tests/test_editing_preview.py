"""Parte 5, Etapa 4: edição pela timeline com prévia antes de aplicar."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import src.pipeline as pipeline
from src import images
from src.api.app import create_app
from src.api.store import ProjectStore
from src.broll.planner import ItemBroll, VideoBroll
from src.config import get_settings
from src.editing.preview import limit_plan, list_editor_elements, remove_element
from src.editing.project_schema import com_plano
from src.highlight_captions.planner import ItemDestaque
from src.images import ItemImagem, ItemZoom, PlanoImagens, timeline_signature
from src.project import Project

from .conftest import requires_ffmpeg
from .test_editing_replace import _candidate, _color_video, _frames, _job, _video


@pytest.mark.parametrize("element_id", ["img_001", "broll_002", "zoom_003", "highlight_004"])
def test_editor_lists_four_types_and_remove_keeps_stable_ids(tmp_path, element_id):
    plan = PlanoImagens(
        assinatura="teste",
        itens=[ItemImagem(
            id=1, indice=0, clipe=0, palavra="fruta", query="fruta",
            inicio=1, duracao=1, candidatos=[_candidate(tmp_path, "red", (255, 0, 0))],
        )],
        broll=[ItemBroll(
            id=2, clipe=0, trecho_inicio_palavra=0, trecho_fim_palavra=1,
            texto="frase", query="frase", inicio=2, duracao_max=1,
            motivo="teste", aprovado=True,
            video=VideoBroll(arquivo=tmp_path / "video.mp4", fonte="pexels", id="2", pagina=""),
        )],
        zooms=[ItemZoom(
            id=3, indice=0, clipe=0, palavra="fruta", inicio=3, duracao=1,
        )],
        destaques=[ItemDestaque(
            id=4, clipe=0, segmento=0, trecho_inicio_palavra=0,
            trecho_fim_palavra=1, inicio=4, fim=4.4, texto="frase curta",
            motivo="teste", duracao_permanencia=1.2,
        )],
    )
    project = Project()
    project.documento = com_plano(project.documento, plan)
    listed = list_editor_elements(project, {
        "imagens": True, "broll": True, "zooms": True,
        "reenquadrar": True, "legendas_destaque": True,
    })
    assert {item.id for item in listed.elements} == {
        "img_001", "broll_002", "zoom_003", "highlight_004",
    }
    assert all(item.editavel for item in listed.elements)
    highlight = next(item for item in listed.elements if item.id == "highlight_004")
    assert highlight.fim == pytest.approx(5.6)
    removed = remove_element(plan, element_id)
    changed = next(item for item in (
        *removed.itens, *removed.broll, *removed.zooms, *removed.destaques,
    ) if item.id == int(element_id.rsplit("_", 1)[1]))
    original = next(item for item in (
        *plan.itens, *plan.broll, *plan.zooms, *plan.destaques,
    ) if item.id == changed.id)
    assert getattr(changed, "ativa", getattr(changed, "ativo", None)) is False
    assert getattr(original, "ativa", getattr(original, "ativo", None)) is True
    assert limit_plan(plan, (4, 5)).itens[0].ativa is False
    with pytest.raises(ValueError, match="removido"):
        remove_element(removed, element_id)


@requires_ffmpeg
def test_preview_then_apply_image_alternative_and_undo(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "face_tracks", lambda *_: {})
    monkeypatch.setattr(images, "download", lambda url, settings=None: Path(url))
    source = tmp_path / "camera.mp4"
    _video(source)
    folder = tmp_path / "clips"
    folder.mkdir()
    (folder / "camera.mp4").write_bytes(source.read_bytes())
    red = _candidate(tmp_path, "red", (255, 0, 0))
    blue = _candidate(tmp_path, "blue", (0, 0, 255))

    with TestClient(create_app()) as client:
        pid = client.post("/api/projects", json={"nome": "prévia"}).json()["id"]
        assert client.get(f"/api/projects/{pid}/editor").json()["elements"] == []
        assert client.post(
            f"/api/projects/{pid}/clips/import", json={"pasta": str(folder)}
        ).status_code == 200
        store = ProjectStore(get_settings().data_dir / "projects")
        project = store.load(pid)
        plan = PlanoImagens(
            assinatura=timeline_signature(project),
            itens=[ItemImagem(
                id=1, indice=0, clipe=0, palavra="fruta", query="fruit",
                inicio=0.5, duracao=1, candidatos=[red, blue],
            )],
        )
        store.save_plan(pid, plan)
        options = {
            "cortes": False, "reenquadrar": False, "legendas_continuas": False,
            "imagens": True, "zooms": False,
        }
        job = client.post(
            f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": options}
        ).json()
        assert _job(client, job["id"])["status"] == "concluido"
        final = store.saida_dir(pid) / "final.mp4"
        before_bytes = final.read_bytes()
        before = _frames(final)
        editor = client.get(f"/api/projects/{pid}/editor")
        assert editor.status_code == 200
        assert editor.json()["elements"][0]["id"] == "img_001"
        assert editor.json()["elements"][0]["editavel"] is True

        preview_job = client.post(
            f"/api/projects/{pid}/editor/img_001/preview",
            json={"action": "replace", "mode": "alternativa", "index": 1},
        )
        assert preview_job.status_code == 202, preview_job.text
        preview_done = _job(client, preview_job.json()["id"])
        assert preview_done["status"] == "concluido", preview_done
        preview = preview_done["resultado"]
        assert final.read_bytes() == before_bytes
        assert store.load_plan(pid).itens[0].candidato.id == "red"
        video = client.get(preview["preview_url"])
        assert video.status_code == 200 and video.content
        preview_path = store.dir(pid) / ".editor_previews" / preview["token"] / "preview.mp4"
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,width,height",
                "-of", "json", str(preview_path),
            ],
            check=True, capture_output=True, text=True,
        )
        metadata = json.loads(probe.stdout)
        assert float(metadata["format"]["duration"]) < 6
        assert [s["codec_type"] for s in metadata["streams"]] == ["video"]
        assert metadata["streams"][0]["width"] <= 360
        assert metadata["streams"][0]["height"] <= 640

        apply = client.post(f"/api/projects/{pid}/editor/previews/{preview['token']}/apply")
        assert apply.status_code == 202, apply.text
        applied = _job(client, apply.json()["id"])
        assert applied["status"] == "concluido", applied
        after = _frames(final)
        assert np.array_equal(before[120], after[120])
        assert not np.array_equal(before[30], after[30])
        assert store.load_plan(pid).itens[0].candidato.id == "blue"
        assert client.get(f"/api/projects/{pid}/history").json()["can_undo"] is True
        assert client.get(preview["preview_url"]).status_code == 404

        undo = client.post(f"/api/projects/{pid}/history/undo")
        assert undo.status_code == 202
        undone = _job(client, undo.json()["id"])
        assert undone["status"] == "concluido", undone
        assert store.load_plan(pid).itens[0].candidato.id == "red"
        assert np.array_equal(before, _frames(final))


@requires_ffmpeg
def test_preview_removes_element_and_rejects_unchanged_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "face_tracks", lambda *_: {})
    monkeypatch.setattr(images, "download", lambda url, settings=None: Path(url))
    source = tmp_path / "camera.mp4"
    _video(source)
    folder = tmp_path / "clips"
    folder.mkdir()
    (folder / "camera.mp4").write_bytes(source.read_bytes())
    with TestClient(create_app()) as client:
        pid = client.post("/api/projects", json={"nome": "remoção"}).json()["id"]
        client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(folder)})
        store = ProjectStore(get_settings().data_dir / "projects")
        plan = PlanoImagens(
            assinatura=timeline_signature(store.load(pid)),
            itens=[ItemImagem(
                id=1, indice=0, clipe=0, palavra="fruta", query="fruit",
                inicio=0.5, duracao=1,
                candidatos=[_candidate(tmp_path, "red", (255, 0, 0))],
            )],
        )
        store.save_plan(pid, plan)
        job = client.post(f"/api/projects/{pid}/jobs", json={
            "tipo": "gerar", "opcoes": {
                "cortes": False, "reenquadrar": False, "legendas_continuas": False,
                "imagens": True, "zooms": False,
            },
        }).json()
        assert _job(client, job["id"])["status"] == "concluido"
        preview = client.post(
            f"/api/projects/{pid}/editor/img_001/preview", json={"action": "remove"}
        )
        assert preview.status_code == 202
        done = _job(client, preview.json()["id"])
        assert done["status"] == "concluido", done
        token = done["resultado"]["token"]
        final = store.saida_dir(pid) / "final.mp4"
        before_bytes = final.read_bytes()
        unchanged = client.post(f"/api/projects/{pid}/editor/img_001/preview", json={
            "action": "replace", "mode": "alternativa", "index": 0,
        })
        assert unchanged.status_code == 202
        # Aguarda o job inválido antes de aplicar a prévia anterior.
        assert _job(client, unchanged.json()["id"])["status"] == "erro"
        assert final.read_bytes() == before_bytes

        final.write_bytes(before_bytes + b"stale")
        stale = client.post(f"/api/projects/{pid}/editor/previews/{token}/apply")
        assert stale.status_code == 409
        final.write_bytes(before_bytes)

        apply = client.post(f"/api/projects/{pid}/editor/previews/{token}/apply")
        assert apply.status_code == 202
        assert _job(client, apply.json()["id"])["status"] == "concluido"
        assert store.load_plan(pid).itens[0].ativa is False
        assert client.get(f"/api/projects/{pid}/editor").json()["elements"][0]["ativo"] is False
        entries = client.get(f"/api/projects/{pid}/history").json()["entries"]
        assert "removido" in entries[-1]["summary"]


@requires_ffmpeg
def test_broll_preview_remove_changes_only_cutaway_after_apply(tmp_path, monkeypatch):
    from src.broll import source as broll_source

    settings = get_settings().model_copy(update={"output_width": 160, "output_height": 288})
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(broll_source, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "face_tracks", lambda *_: {})
    camera = tmp_path / "camera.mp4"
    cutaway = tmp_path / "blue.mp4"
    _video(camera)
    _color_video(cutaway, "blue")
    folder = tmp_path / "clips"
    folder.mkdir()
    (folder / "camera.mp4").write_bytes(camera.read_bytes())
    with TestClient(create_app()) as client:
        pid = client.post("/api/projects", json={"nome": "prévia B-roll"}).json()["id"]
        client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(folder)})
        store = ProjectStore(get_settings().data_dir / "projects")
        plan = PlanoImagens(
            assinatura=timeline_signature(store.load(pid)),
            broll=[ItemBroll(
                id=1, clipe=0, trecho_inicio_palavra=0, trecho_fim_palavra=1,
                texto="frase", query="azul", inicio=2, duracao_max=2,
                motivo="teste", aprovado=True,
                video=VideoBroll(
                    arquivo=cutaway, fonte="pexels", id="blue", pagina="https://example.com"
                ),
            )],
        )
        store.save_plan(pid, plan)
        job = client.post(f"/api/projects/{pid}/jobs", json={
            "tipo": "gerar", "opcoes": {
                "cortes": False, "reenquadrar": True, "legendas_continuas": False,
                "imagens": False, "zooms": False, "broll": True,
            },
        }).json()
        assert _job(client, job["id"])["status"] == "concluido"
        final = store.saida_dir(pid) / "final.mp4"
        before = _frames(final)
        before_bytes = final.read_bytes()
        preview_job = client.post(
            f"/api/projects/{pid}/editor/broll_001/preview", json={"action": "remove"}
        )
        assert preview_job.status_code == 202, preview_job.text
        done = _job(client, preview_job.json()["id"])
        assert done["status"] == "concluido", done
        assert final.read_bytes() == before_bytes
        token = done["resultado"]["token"]
        apply = client.post(f"/api/projects/{pid}/editor/previews/{token}/apply")
        assert apply.status_code == 202
        applied = _job(client, apply.json()["id"])
        assert applied["status"] == "concluido", applied
        after = _frames(final)
        assert not np.array_equal(before[90], after[90])
        assert np.array_equal(before[15], after[15])
        assert np.array_equal(before[160], after[160])
        assert store.load_plan(pid).broll[0].ativo is False
