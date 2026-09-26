"""Parte 5, Etapa 6: conversa, prévia automática e confirmação pela API."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

import src.editing.replace as replace_module
import src.pipeline as pipeline
from src import images
from src.api.app import create_app
from src.api.store import ProjectStore
from src.config import get_settings
from src.images import ItemImagem, ItemZoom, PlanoImagens, timeline_signature

from .conftest import make_video, requires_ffmpeg
from .test_editing_replace import _candidate, _frames, _job, _video


@requires_ffmpeg
def test_chat_image_swap_preview_apply_and_non_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "face_tracks", lambda *_: {})
    monkeypatch.setattr(images, "download", lambda url, settings=None: Path(url))
    monkeypatch.setattr(
        "src.editing.chat.tools.global_words",
        lambda *_: (_ for _ in ()).throw(AssertionError("transcrição desnecessária")),
    )
    source = tmp_path / "camera.mp4"
    _video(source)
    folder = tmp_path / "clips"
    folder.mkdir()
    (folder / "camera.mp4").write_bytes(source.read_bytes())
    red = _candidate(tmp_path, "red", (255, 0, 0))
    blue = _candidate(tmp_path, "blue", (0, 0, 255))
    monkeypatch.setattr(replace_module, "search_images", lambda *_: [blue])
    calls = iter([
        {"content": "", "tool_calls": [{
            "name": "listar_elementos", "args": {"filtro": "fruta"}, "id": "list_1",
        }]},
        {"content": "", "tool_calls": [{
            "name": "trocar_imagem", "args": {
                "id": "img_001", "nova_query": "blue fruit",
            }, "id": "edit_1",
        }]},
        {"content": "Propus a troca da img_001.", "tool_calls": []},
    ])
    monkeypatch.setattr(
        "src.editing.chat.agent.client.run_tool_turn",
        lambda *_args, **_kwargs: next(calls),
    )
    with TestClient(create_app()) as client:
        pid = client.post("/api/projects", json={"nome": "chat imagem"}).json()["id"]
        client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(folder)})
        store = ProjectStore(get_settings().data_dir / "projects")
        plan = PlanoImagens(
            assinatura=timeline_signature(store.load(pid)),
            itens=[ItemImagem(
                id=1, indice=0, clipe=0, palavra="fruta", query="red fruit",
                inicio=0.5, duracao=1.5, candidatos=[red],
            )],
        )
        store.save_plan(pid, plan)
        render = client.post(f"/api/projects/{pid}/jobs", json={
            "tipo": "gerar", "opcoes": {
                "cortes": False, "reenquadrar": False, "legendas_continuas": False,
                "imagens": True, "zooms": False,
            },
        }).json()
        assert _job(client, render["id"])["status"] == "concluido"
        final = store.saida_dir(pid) / "final.mp4"
        before_bytes = final.read_bytes()
        before_frames = _frames(final)
        chat = client.post(f"/api/projects/{pid}/chat", json={
            "message": "Troque a imagem da fruta por uma azul",
        })
        assert chat.status_code == 202, chat.text
        done = _job(client, chat.json()["id"])
        assert done["status"] == "concluido", done
        result = done["resultado"]
        assert result["acoes"] == [{
            "ferramenta": "trocar_imagem", "id": "img_001", "tipo": "imagem",
        }]
        preview = result["preview"]
        assert client.get(preview["preview_url"]).status_code == 200
        assert final.read_bytes() == before_bytes
        state = client.get(f"/api/projects/{pid}/chat").json()
        assert state["pending_token"] == preview["token"]
        assert state["messages"][-1]["actions"] == result["acoes"]
        assert state["messages"][-1]["preview"]["token"] == preview["token"]
        assert "prévia" in state["messages"][-1]["text"].lower()

        apply = client.post(f"/api/projects/{pid}/editor/previews/{preview['token']}/apply")
        assert apply.status_code == 202
        applied = _job(client, apply.json()["id"])
        assert applied["status"] == "concluido", applied
        assert store.load_plan(pid).itens[0].query == "blue fruit"
        after = _frames(final)
        assert not np.array_equal(before_frames[30], after[30])
        assert np.array_equal(before_frames[120], after[120])
        state = client.get(f"/api/projects/{pid}/chat").json()
        assert state["pending_token"] is None
        assert state["messages"][-1]["applied"] is True
        assert client.get(f"/api/projects/{pid}/history").json()["can_undo"] is True
        applied_bytes = final.read_bytes()

        for request, expected in (
            ("melhora essa parte", "Qual elemento"),
            ("refaz todas as imagens", "replanejamento completo"),
        ):
            response = client.post(f"/api/projects/{pid}/chat", json={"message": request})
            assert response.status_code == 202
            finished = _job(client, response.json()["id"])
            assert finished["status"] == "concluido", finished
            assert expected in finished["resultado"]["resposta"]
            assert finished["resultado"]["acoes"] == []
            assert finished["resultado"]["preview"] is None
        assert store.load_plan(pid).itens[0].query == "blue fruit"
        assert final.read_bytes() == applied_bytes


@requires_ffmpeg
def test_chat_remove_zoom_uses_preview_and_changes_render(tmp_path, monkeypatch):
    from .test_zoom import rosto

    settings = get_settings().model_copy(update={"output_width": 160, "output_height": 288})
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "face_tracks", lambda *_: {0: rosto(
        n=180, largura=160, altura=288,
    )})
    monkeypatch.setattr(
        "src.editing.chat.tools.global_words",
        lambda *_: (_ for _ in ()).throw(AssertionError("transcrição desnecessária")),
    )
    source = make_video(tmp_path / "camera.mp4", duration=6, size="160x288")
    folder = tmp_path / "clips"
    folder.mkdir()
    (folder / "camera.mp4").write_bytes(source.read_bytes())
    calls = iter([
        {"content": "", "tool_calls": [{
            "name": "listar_elementos", "args": {"filtro": "zoom"}, "id": "list_zoom",
        }]},
        {"content": "", "tool_calls": [{
            "name": "remover_elemento", "args": {"id": "zoom_001"}, "id": "remove_zoom",
        }]},
        {"content": "Propus remover zoom_001.", "tool_calls": []},
    ])
    monkeypatch.setattr(
        "src.editing.chat.agent.client.run_tool_turn",
        lambda *_args, **_kwargs: next(calls),
    )
    with TestClient(create_app()) as client:
        pid = client.post("/api/projects", json={"nome": "chat zoom"}).json()["id"]
        client.post(f"/api/projects/{pid}/clips/import", json={"pasta": str(folder)})
        store = ProjectStore(get_settings().data_dir / "projects")
        plan = PlanoImagens(
            assinatura=timeline_signature(store.load(pid)),
            zooms=[ItemZoom(
                id=1, indice=0, clipe=0, palavra="ênfase", inicio=2,
                duracao=1.5,
            )],
        )
        store.save_plan(pid, plan)
        render = client.post(f"/api/projects/{pid}/jobs", json={
            "tipo": "gerar", "opcoes": {
                "cortes": False, "reenquadrar": True, "legendas_continuas": False,
                "imagens": False, "zooms": True,
            },
        }).json()
        first = _job(client, render["id"])
        assert first["status"] == "concluido", first
        final = store.saida_dir(pid) / "final.mp4"
        before = _frames(final)
        chat = client.post(f"/api/projects/{pid}/chat", json={
            "message": "Tira o zoom dessa parte",
        })
        assert chat.status_code == 202
        done = _job(client, chat.json()["id"])
        assert done["status"] == "concluido", done
        preview = done["resultado"]["preview"]
        assert preview["elemento"] == "zoom_001"
        assert client.get(preview["preview_url"]).status_code == 200
        apply = client.post(f"/api/projects/{pid}/editor/previews/{preview['token']}/apply")
        assert apply.status_code == 202
        applied = _job(client, apply.json()["id"])
        assert applied["status"] == "concluido", applied
        after = _frames(final)
        assert store.load_plan(pid).zooms[0].ativo is False
        assert not np.array_equal(before[80], after[80])
        assert np.array_equal(before[15], after[15])
