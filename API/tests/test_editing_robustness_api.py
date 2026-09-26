"""Parte 5, Etapa 7: edições seguidas não corrompem o vídeo, cache ou histórico."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

import src.editing.replace as replace_module
import src.pipeline as pipeline
from src import images
from src.api.app import create_app
from src.api.store import ProjectStore
from src.broll.planner import ItemBroll, VideoBroll
from src.config import get_settings
from src.editing.history import rendered_matches, video_sha256
from src.editing.project_schema import plano_do_documento
from src.images import ItemImagem, PlanoImagens, timeline_signature

from .conftest import make_video, requires_ffmpeg
from .test_editing_replace import _candidate, _color_video, _frames, _job


@requires_ffmpeg
def test_five_mixed_edits_preserve_neighbor_broll_history_and_cache(tmp_path, monkeypatch):
    """Cinco alterações reais, duas por chat, com prévia e undo/redo no final."""
    settings = get_settings().model_copy(update={"output_width": 160, "output_height": 288})
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "face_tracks", lambda *_: {})
    monkeypatch.setattr(images, "download", lambda url, settings=None: Path(url))
    monkeypatch.setattr(
        "src.editing.chat.tools.global_words",
        lambda *_: (_ for _ in ()).throw(AssertionError("transcrição desnecessária")),
    )

    camera = make_video(tmp_path / "camera.mp4", duration=12, size="160x288")
    cutaway = tmp_path / "cutaway.mp4"
    _color_video(cutaway, "blue")
    folder = tmp_path / "clips"
    folder.mkdir()
    (folder / "camera.mp4").write_bytes(camera.read_bytes())
    red = _candidate(tmp_path, "red", (255, 0, 0))
    green = _candidate(tmp_path, "green", (0, 255, 0))
    yellow = _candidate(tmp_path, "yellow", (255, 255, 0))
    blue = _candidate(tmp_path, "blue", (0, 0, 255))
    purple = _candidate(tmp_path, "purple", (160, 0, 160))
    search_results = {"fruta azul": blue, "fruta roxa": purple}
    monkeypatch.setattr(
        replace_module, "search_images", lambda query: [search_results[query]]
    )
    turns = iter([
        {"content": "", "tool_calls": [{"name": "trocar_imagem", "id": "edit_blue",
                                     "args": {"id": "img_002", "nova_query": "fruta azul"}}]},
        {"content": "Troca proposta para img_002.", "tool_calls": []},
        {"content": "", "tool_calls": [{"name": "trocar_imagem", "id": "edit_purple",
                                     "args": {"id": "img_002", "nova_query": "fruta roxa"}}]},
        {"content": "Nova troca proposta para img_002.", "tool_calls": []},
    ])
    monkeypatch.setattr(
        "src.editing.chat.agent.client.run_tool_turn",
        lambda *_args, **_kwargs: next(turns),
    )

    with TestClient(create_app()) as client:
        pid = client.post("/api/projects", json={"nome": "sequência edição"}).json()["id"]
        assert client.post(
            f"/api/projects/{pid}/clips/import", json={"pasta": str(folder)}
        ).status_code == 200
        store = ProjectStore(settings.data_dir / "projects")
        plan = PlanoImagens(
            assinatura=timeline_signature(store.load(pid)),
            itens=[
                ItemImagem(id=1, indice=0, clipe=0, palavra="maçã", query="fruta vermelha",
                           inicio=0.5, duracao=1.5, candidatos=[red, green]),
                ItemImagem(id=2, indice=1, clipe=0, palavra="banana", query="fruta amarela",
                           inicio=7, duracao=1.5, candidatos=[yellow]),
            ],
            broll=[ItemBroll(
                id=1, clipe=0, trecho_inicio_palavra=0, trecho_fim_palavra=1,
                texto="cena vizinha", query="cena azul", inicio=3, duracao_max=2,
                motivo="teste", aprovado=True,
                video=VideoBroll(
                    arquivo=cutaway, fonte="pexels", id="blue-cutaway",
                    pagina="https://example.com/cutaway",
                ),
            )],
        )
        store.save_plan(pid, plan)
        render = client.post(f"/api/projects/{pid}/jobs", json={
            "tipo": "gerar", "opcoes": {
                "cortes": False, "reenquadrar": True, "legendas_continuas": False,
                "imagens": True, "zooms": False, "broll": True,
            },
        })
        assert render.status_code == 202, render.text
        assert _job(client, render.json()["id"])["status"] == "concluido"
        final = store.saida_dir(pid) / "final.mp4"
        baseline = _frames(final)
        prior = baseline
        baseline_broll = baseline[90:150].copy()
        baseline_gap = baseline[150:210].copy()
        expected_ids = ["img_001", "img_002", "img_001", "img_002", "img_001"]
        changed_frames = [30, 225, 30, 225, 30]
        completed = []

        def checked(job_response, step: int):
            nonlocal prior
            assert job_response.status_code == 202, job_response.text
            done = _job(client, job_response.json()["id"])
            assert done["status"] == "concluido", done
            assert done["resultado"]["segmentos_reutilizados"] > 0
            current = _frames(final)
            assert not np.array_equal(prior[changed_frames[step]], current[changed_frames[step]])
            assert np.array_equal(current[90:150], baseline_broll)
            assert np.array_equal(current[150:210], baseline_gap)
            assert rendered_matches(store.dir(pid), store.load(pid), final) is True
            current_project = store.load(pid)
            current_plan = store.load_plan(pid)
            assert plano_do_documento(current_project.documento) == current_plan
            for item in current_plan.itens:
                assert current_project.documento.por_id(f"img_{item.id:03d}") is not None
                if item.ativa:
                    assert item.fim <= current_plan.broll[0].inicio or (
                        current_plan.broll[0].fim <= item.inicio
                    )
            assert current_project.documento.por_id("broll_001") is not None
            history = client.get(f"/api/projects/{pid}/history").json()
            assert len(history["entries"]) == step + 2
            assert history["cursor"] == step + 1
            assert history["entries"][-1]["element_ids"] == [expected_ids[step]]
            assert history["entries"][-1]["video_sha256"] == video_sha256(final)
            manifest = json.loads(
                (store.dir(pid) / "render_cache" / "manifest.json").read_text(encoding="utf-8")
            )
            assert manifest["segmentos"]
            assert all(
                (store.dir(pid) / "render_cache" / f"{segment['hash']}.mov").is_file()
                for segment in manifest["segmentos"]
            )
            completed.append(final.read_bytes())
            prior = current

        # 1: troca manual direta por alternativa salva.
        checked(client.post(
            f"/api/projects/{pid}/replace/img_001",
            json={"modo": "alternativa", "indice": 1},
        ), 0)

        def chat_swap(message: str, step: int):
            before = final.read_bytes()
            request = client.post(f"/api/projects/{pid}/chat", json={"message": message})
            assert request.status_code == 202, request.text
            response = _job(client, request.json()["id"])
            assert response["status"] == "concluido", response
            assert response["resultado"]["acoes"][0]["id"] == "img_002"
            preview = response["resultado"]["preview"]
            assert client.get(preview["preview_url"]).status_code == 200
            assert final.read_bytes() == before
            checked(client.post(
                f"/api/projects/{pid}/editor/previews/{preview['token']}/apply"
            ), step)
            assert client.get(f"/api/projects/{pid}/chat").json()["messages"][-1]["applied"]

        # 2: troca via agente com confirmação da prévia.
        chat_swap("troca a imagem da banana por uma fruta azul", 1)

        # 3: remoção manual por prévia.
        before = final.read_bytes()
        preview_job = client.post(
            f"/api/projects/{pid}/editor/img_001/preview", json={"action": "remove"}
        )
        assert preview_job.status_code == 202, preview_job.text
        preview_result = _job(client, preview_job.json()["id"])
        assert preview_result["status"] == "concluido", preview_result
        assert final.read_bytes() == before
        checked(client.post(
            f"/api/projects/{pid}/editor/previews/{preview_result['resultado']['token']}/apply"
        ), 2)
        assert store.load_plan(pid).itens[0].ativa is False

        # 4: segunda troca por chat após uma edição manual; conversa anterior continua.
        chat_swap("troca a imagem da banana por uma fruta roxa", 3)
        chat_messages = client.get(f"/api/projects/{pid}/chat").json()["messages"]
        assert len(chat_messages) == 4
        assert [message["role"] for message in chat_messages] == [
            "user", "assistant", "user", "assistant",
        ]
        assert "fruta azul" in chat_messages[0]["text"]
        assert "fruta roxa" in chat_messages[2]["text"]
        assert [chat_messages[i]["actions"][0]["id"] for i in (1, 3)] == [
            "img_002", "img_002",
        ]
        assert chat_messages[1]["applied"] and chat_messages[3]["applied"]

        # 5: reativação manual da imagem removida por alternativa salva.
        checked(client.post(
            f"/api/projects/{pid}/replace/img_001",
            json={"modo": "alternativa", "indice": 0},
        ), 4)

        final_plan = store.load_plan(pid)
        assert [item.id for item in final_plan.itens] == [1, 2]
        assert final_plan.itens[0].ativa and final_plan.itens[0].candidato.id == "red"
        assert final_plan.itens[1].candidato.id == "purple"
        assert final_plan.broll[0].ativo and final_plan.broll[0].video.id == "blue-cutaway"
        assert np.array_equal(prior[30], baseline[30])
        assert not np.array_equal(prior[225], baseline[225])
        ids = [element.id for element in store.load(pid).documento.elementos]
        assert len(ids) == len(set(ids))
        assert set(expected_ids + ["broll_001"]).issubset(ids)
        assert final_plan.itens[0].fim < final_plan.broll[0].inicio
        assert final_plan.broll[0].fim < final_plan.itens[1].inicio

        # Navegar no histórico após a sequência restaura os bytes de cada versão.
        undo = client.post(f"/api/projects/{pid}/history/undo")
        assert undo.status_code == 202
        assert _job(client, undo.json()["id"])["status"] == "concluido"
        assert final.read_bytes() == completed[-2]
        redo = client.post(f"/api/projects/{pid}/history/redo")
        assert redo.status_code == 202
        assert _job(client, redo.json()["id"])["status"] == "concluido"
        assert final.read_bytes() == completed[-1]
