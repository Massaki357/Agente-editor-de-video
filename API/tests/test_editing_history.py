"""Parte 5, Etapa 3: versões limitadas e desfazer/refazer do vídeo."""

from __future__ import annotations

import io
import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

import src.pipeline as pipeline
from src.api.app import create_app
from src.api.store import ProjectStore
from src.config import get_settings
from src.editing.history import history_state, navigation_target, record_edit, set_cursor
from src.editing.project_schema import DocumentoEdicao, Elemento
from src.images import ItemImagem, PlanoImagens, timeline_signature
from src.project import Project

from .conftest import requires_ffmpeg
from .test_editing_replace import _candidate, _frames, _job, _video


def _project(query: str) -> Project:
    return Project(documento=DocumentoEdicao(elementos=[Elemento(
        id="img_001", tipo="imagem", inicio=0, fim=1,
        dados={"query": query, "escolhida": 0, "candidatos": [{"fonte": "upload", "id": query}]},
    )]))


def test_history_cap_branch_and_diff(tmp_path):
    root = tmp_path / "project"
    old = _project("original")
    first = _project("primeira")
    second = _project("segunda")
    third = _project("terceira")
    state = record_edit(
        root, old, first, {"img_001"}, max_versions=3,
        before_video_hash="old", after_video_hash="first",
    )
    assert [entry.version for entry in state.entries] == [0, 1]
    assert state.entries[1].diff["img_001"]["antes"]["query"] == "original"
    assert state.entries[1].diff["img_001"]["depois"]["query"] == "primeira"
    state = record_edit(
        root, first, second, {"img_001"}, max_versions=3,
        before_video_hash="first", after_video_hash="second",
    )
    assert state.can_undo and not state.can_redo
    target, ids, cursor, digest = navigation_target(root, second, "undo")
    assert target == first and ids == {"img_001"} and cursor == 1 and digest == "first"
    set_cursor(root, 2, cursor)
    assert history_state(root, first).can_redo
    state = record_edit(
        root, first, third, {"img_001"}, max_versions=3,
        before_video_hash="first", after_video_hash="third",
    )
    assert [entry.version for entry in state.entries] == [0, 1, 3]
    assert not (root / ".history" / "v000002.json").exists()
    before_hash = "third"
    for i in range(6):
        following = _project(f"extra{i}")
        state = record_edit(
            root, third, following, {"img_001"}, max_versions=3,
            before_video_hash=before_hash, after_video_hash=f"extra{i}",
        )
        third = following
        before_hash = f"extra{i}"
    assert len(state.entries) == 3
    assert len(list((root / ".history").glob("v*.json"))) == 3
    assert state.cursor == 2
    assert not state.can_redo
    assert not history_state(root, _project("outra edição")).can_undo


@requires_ffmpeg
def test_undo_redo_restores_project_and_video(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "face_tracks", lambda *_: {})
    from src import images

    monkeypatch.setattr(images, "download", lambda url, settings=None: Path(url))
    clip = tmp_path / "camera.mp4"
    _video(clip)
    original = _candidate(tmp_path, "original", (255, 0, 0))
    with TestClient(create_app()) as client:
        pid = client.post("/api/projects", json={"nome": "histórico"}).json()["id"]
        folder = tmp_path / "clips"
        folder.mkdir()
        (folder / "camera.mp4").write_bytes(clip.read_bytes())
        assert client.post(
            f"/api/projects/{pid}/clips/import", json={"pasta": str(folder)}
        ).status_code == 200
        store = ProjectStore(get_settings().data_dir / "projects")
        project = store.load(pid)
        store.save_plan(pid, PlanoImagens(
            assinatura=timeline_signature(project),
            itens=[ItemImagem(
                id=1, indice=0, clipe=0, palavra="fruta", query="fruit",
                inicio=0.5, duracao=1, candidatos=[original],
            )],
        ))
        options = {
            "cortes": False, "reenquadrar": False, "legendas_continuas": False,
            "imagens": True, "zooms": False,
        }
        first = client.post(
            f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": options}
        )
        assert _job(client, first.json()["id"])["status"] == "concluido"
        final = store.saida_dir(pid) / "final.mp4"
        before_bytes = final.read_bytes()
        before_frames = _frames(final)
        before_project = store.load(pid).model_dump(mode="json")
        pending_plan = store.load_plan(pid)
        pending_plan.itens[0].query = "busca pendente"
        store.save_plan(pid, pending_plan)
        refused = client.post(
            f"/api/projects/{pid}/replace/img_001",
            json={"modo": "alternativa", "indice": 0},
        )
        assert refused.status_code == 409
        assert final.read_bytes() == before_bytes
        store.save_plan(pid, PlanoImagens(
            assinatura=timeline_signature(store.load(pid)),
            itens=[ItemImagem(
                id=1, indice=0, clipe=0, palavra="fruta", query="fruit",
                inicio=0.5, duracao=1, candidatos=[original],
            )],
        ))
        same = client.post(
            f"/api/projects/{pid}/replace/img_001",
            json={"modo": "alternativa", "indice": 0},
        )
        assert same.status_code == 202
        assert _job(client, same.json()["id"])["status"] == "erro"
        assert final.read_bytes() == before_bytes
        assert client.get(f"/api/projects/{pid}/history").json()["entries"] == []
        payload = io.BytesIO()
        Image.new("RGB", (120, 80), (0, 255, 0)).save(payload, format="PNG")
        replace = client.post(
            f"/api/projects/{pid}/replace/img_001/upload",
            files={"file": ("verde.png", payload.getvalue(), "image/png")},
        )
        assert replace.status_code == 202
        replaced = _job(client, replace.json()["id"])
        assert replaced["status"] == "concluido", replaced
        changed_frames = _frames(final)
        changed_bytes = final.read_bytes()
        changed_project = store.load(pid).model_dump(mode="json")
        assert not np.array_equal(before_frames[30], changed_frames[30])
        history = client.get(f"/api/projects/{pid}/history").json()
        assert history["can_undo"] and not history["can_redo"]
        assert len(history["entries"]) == 2
        assert history["entries"][1]["element_ids"] == ["img_001"]
        assert history["entries"][1]["diff"]["img_001"]["depois"]["fonte"] == "upload"

        # Arquivo de origem removido: não publicar vídeo sem a imagem restaurada.
        original_bytes = Path(original.url).read_bytes()
        Path(original.url).unlink()
        missing = client.post(f"/api/projects/{pid}/history/undo")
        assert missing.status_code == 202
        assert _job(client, missing.json()["id"])["status"] == "erro"
        assert store.load(pid).model_dump(mode="json") == changed_project
        assert final.read_bytes() == changed_bytes
        assert client.get(f"/api/projects/{pid}/history").json()["cursor"] == 1
        Path(original.url).write_bytes(original_bytes)

        undo = client.post(f"/api/projects/{pid}/history/undo")
        assert undo.status_code == 202
        undone = _job(client, undo.json()["id"])
        assert undone["status"] == "concluido", undone
        assert undone["resultado"]["segmentos_reutilizados"] > 0
        assert store.load(pid).model_dump(mode="json") == before_project
        assert final.read_bytes() == before_bytes
        assert np.array_equal(_frames(final), before_frames)
        assert client.get(f"/api/projects/{pid}/history").json()["can_redo"]
        assert client.post(f"/api/projects/{pid}/history/undo").status_code == 409

        redo = client.post(f"/api/projects/{pid}/history/redo")
        assert redo.status_code == 202
        redone = _job(client, redo.json()["id"])
        assert redone["status"] == "concluido", redone
        assert store.load(pid).model_dump(mode="json") == changed_project
        assert final.read_bytes() == changed_bytes
        assert np.array_equal(_frames(final), changed_frames)
        assert client.post(f"/api/projects/{pid}/history/redo").status_code == 409

        # Falha de render não muda projeto, vídeo nem posição no histórico.
        original_render = pipeline.render_project

        def failed(*args, **kwargs):
            raise RuntimeError("falha de render sintética")

        monkeypatch.setattr(pipeline, "render_project", failed)
        rejected = client.post(f"/api/projects/{pid}/history/undo")
        assert rejected.status_code == 202
        assert _job(client, rejected.json()["id"])["status"] == "erro"
        assert store.load(pid).model_dump(mode="json") == changed_project
        assert final.read_bytes() == changed_bytes
        assert client.get(f"/api/projects/{pid}/history").json()["cursor"] == 1

        # Um projeto ocupado não aceita outra navegação simultânea.
        def slowed(*args, **kwargs):
            time.sleep(0.4)
            return original_render(*args, **kwargs)

        monkeypatch.setattr(pipeline, "render_project", slowed)
        pending = client.post(f"/api/projects/{pid}/history/undo")
        assert pending.status_code == 202
        assert client.post(f"/api/projects/{pid}/history/redo").status_code == 409
        assert _job(client, pending.json()["id"])["status"] == "concluido"
        monkeypatch.setattr(pipeline, "render_project", original_render)

        # Job de geração que falha após salvar opções novas não invalida o histórico.
        def failed_generate(*args, **kwargs):
            raise RuntimeError("falha sintética na nova geração")

        monkeypatch.setattr(pipeline, "render_project", failed_generate)
        new_options = {**options, "broll_transition": "wipe"}
        failed_job = client.post(
            f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": new_options}
        )
        assert _job(client, failed_job.json()["id"])["status"] == "erro"
        assert client.get(f"/api/projects/{pid}/history").json()["can_redo"]
        monkeypatch.setattr(pipeline, "render_project", original_render)
        reapplied = client.post(f"/api/projects/{pid}/history/redo")
        assert reapplied.status_code == 202
        assert _job(client, reapplied.json()["id"])["status"] == "concluido"
        assert store.load(pid).broll_transition == "wipe"
        assert final.read_bytes() == changed_bytes

        regenerate = client.post(
            f"/api/projects/{pid}/jobs", json={"tipo": "gerar", "opcoes": options}
        )
        assert _job(client, regenerate.json()["id"])["status"] == "concluido"
        assert client.get(f"/api/projects/{pid}/history").json()["entries"] == []
        # Migração de projeto sem checkpoint: renomear não altera o render.
        (store.dir(pid) / ".history" / "rendered.json").unlink()
        assert client.patch(
            f"/api/projects/{pid}", json={"nome": "novo nome"}
        ).status_code == 200
        current_index = store.load_plan(pid).itens[0].escolhida
        old_project = client.post(
            f"/api/projects/{pid}/replace/img_001",
            json={"modo": "alternativa", "indice": current_index},
        )
        assert old_project.status_code == 202
        assert _job(client, old_project.json()["id"])["status"] == "erro"  # sem mudança
        assert (store.dir(pid) / ".history" / "rendered.json").is_file()
