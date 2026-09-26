"""Parte 5, Etapa 5: ferramentas tipadas e loop de tool calling."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

import src.editing.replace as replace_module
from src.broll.planner import ItemBroll, VideoBroll
from src.editing.chat.agent import run_chat
from src.editing.chat.tools import EditSession, WordTiming, execute_tool
from src.editing.project_schema import com_plano, plano_do_documento
from src.images import Candidato, ItemImagem, ItemZoom, PlanoImagens, timeline_signature
from src.llm import client
from src.pipeline import PipelineOptions, limitar_intensidade_zooms
from src.project import Clip, ClipMeta, Project, Timeline


def _session() -> EditSession:
    clip = Clip(
        arquivo="camera.mp4", trechos=[(0, 20)],
        meta=ClipMeta(duracao=20, largura=160, altura=288, fps=30, tem_audio=True),
    )
    project = Project(timeline=Timeline(clipes=[clip]))
    plan = PlanoImagens(
        assinatura=timeline_signature(project),
        itens=[
            ItemImagem(
                id=1, indice=1, clipe=0, palavra="fruta", query="red fruit",
                inicio=2, duracao=2, candidatos=[Candidato(
                    fonte="pexels", id="red", url="https://example.com/red.jpg",
                    miniatura="", autor="", pagina="",
                )],
            ),
            ItemImagem(
                id=2, indice=2, clipe=0, palavra="flor", query="flower",
                inicio=8, duracao=2, candidatos=[Candidato(
                    fonte="pexels", id="flower", url="https://example.com/flower.jpg",
                    miniatura="", autor="", pagina="",
                )],
            ),
        ],
        zooms=[ItemZoom(
            id=1, indice=3, clipe=0, palavra="importante", inicio=5, duracao=1.5,
        )],
    )
    project.documento = com_plano(project.documento, plan)
    return EditSession(project, plan, PipelineOptions(
        cortes=False, imagens=True, zooms=True, reenquadrar=True,
        broll=False, legendas_continuas=False,
    ))


def test_tools_list_replace_and_keep_project_unchanged_on_invalid_action(monkeypatch):
    session = _session()
    original = session.project.model_copy(deep=True)
    entries = execute_tool(session, "listar_elementos", {"filtro": "fruta"})
    assert [item["id"] for item in entries["elementos"]] == ["img_001"]
    monkeypatch.setattr(replace_module, "search_images", lambda *_: [Candidato(
        fonte="pexels", id="blue", url="https://example.com/blue.jpg",
        miniatura="", autor="", pagina="",
    )])
    result = execute_tool(session, "trocar_imagem", {
        "id": "img_001", "nova_query": "blue fruit",
    })
    assert result["alteracao"]["id"] == "img_001"
    assert plano_do_documento(session.project.documento).itens[0].query == "blue fruit"
    assert original.documento.por_id("img_001").dados["query"] == "red fruit"
    assert session.changes == [{"ferramenta": "trocar_imagem", "id": "img_001", "tipo": "imagem"}]
    before = session.plan.model_dump()
    with pytest.raises(ValueError, match="duração"):
        execute_tool(session, "ajustar_duracao", {"id": "img_001", "nova_duracao": 9})
    assert session.plan.model_dump() == before


def test_duration_move_zoom_and_removal_validate_geometry():
    session = _session()
    with pytest.raises(ValueError, match="densidade|conflita"):
        execute_tool(session, "mover_elemento", {"id": "img_002", "novo_inicio": 3})
    assert session.plan.itens[1].inicio == 8
    with pytest.raises(ValidationError):
        execute_tool(session, "ajustar_duracao", {"id": "zoom_001", "nova_duracao": float("inf")})
    with pytest.raises(ValueError, match="limite de zoom"):
        execute_tool(session, "ajustar_intensidade_zoom", {
            "id": "zoom_001", "novo_valor": 1.5,
        })
    execute_tool(session, "ajustar_intensidade_zoom", {
        "id": "zoom_001", "novo_valor": 1.08,
    })
    assert session.plan.zooms[0].intensidade == 1.08
    execute_tool(session, "remover_elemento", {"id": "zoom_001"})
    assert session.plan.zooms[0].ativo is False
    assert session.project.documento.por_id("zoom_001").ativo is False


def test_valid_duration_and_move_preserve_id_and_segment():
    session = _session()
    execute_tool(session, "ajustar_duracao", {"id": "img_001", "nova_duracao": 2.5})
    execute_tool(session, "mover_elemento", {"id": "img_001", "novo_inicio": 2.2})
    assert session.plan.itens[0].fim == pytest.approx(4.7)
    assert session.project.documento.por_id("img_001").inicio == 2.2
    with pytest.raises(ValueError, match="mesmo trecho"):
        execute_tool(session, "mover_elemento", {"id": "img_001", "novo_inicio": 19})
    assert session.plan.itens[0].inicio == 2.2


def test_zoom_intensity_caps_only_selected_peak():
    session = _session()
    execute_tool(session, "ajustar_intensidade_zoom", {
        "id": "zoom_001", "novo_valor": 1.08,
    })
    result = limitar_intensidade_zooms([
        (5, 6.5, 1.15), (12, 13.5, 1.12),
    ], session.plan)
    assert result == [(5, 6.5, 1.08), (12, 13.5, 1.12)]


def test_broll_duration_must_end_at_transcribed_word_boundary(tmp_path):
    session = _session()
    session.options.broll = True
    session.plan.broll.append(ItemBroll(
        id=1, clipe=0, trecho_inicio_palavra=0, trecho_fim_palavra=3,
        texto="uma frase mais completa", query="scene", inicio=12, duracao_max=3,
        motivo="teste", aprovado=True,
        video=VideoBroll(
            arquivo=tmp_path / "scene.mp4", fonte="pexels", id="scene", pagina="",
        ),
    ))
    session.project.documento = com_plano(session.project.documento, session.plan)
    with pytest.raises(ValueError, match="transcrição necessária"):
        execute_tool(session, "ajustar_duracao", {
            "id": "broll_001", "nova_duracao": 2.1,
        })
    words = [
        WordTiming(0, 0, 12, 12.5, "uma"),
        WordTiming(1, 0, 12.5, 13, "frase"),
        WordTiming(2, 0, 13, 14, "mais"),
        WordTiming(3, 0, 14, 15, "completa"),
        WordTiming(4, 0, 15, 16, "fora"),
    ]
    loads = []

    def load_words():
        loads.append(True)
        return words

    session.word_loader = load_words
    with pytest.raises(ValueError, match="fim de uma palavra"):
        execute_tool(session, "ajustar_duracao", {
            "id": "broll_001", "nova_duracao": 2.1,
        })
    assert loads == [True]
    with pytest.raises(ValueError, match="frase original"):
        execute_tool(session, "ajustar_duracao", {
            "id": "broll_001", "nova_duracao": 4,
        })
    execute_tool(session, "ajustar_duracao", {
        "id": "broll_001", "nova_duracao": 1.97,
    })
    assert loads == [True]
    assert session.plan.broll[0].fim == 14
    assert session.plan.broll[0].trecho_fim_palavra == 2
    assert session.plan.broll[0].texto == "uma frase mais"
    with pytest.raises(ValueError, match="replanejamento"):
        execute_tool(session, "mover_elemento", {
            "id": "broll_001", "novo_inicio": 13,
        })
    with pytest.raises(ValueError, match="oculto"):
        execute_tool(session, "mover_elemento", {
            "id": "zoom_001", "novo_inicio": 12,
        })


def test_agent_lists_and_replaces_via_mocked_tool_calls(monkeypatch):
    session = _session()
    monkeypatch.setattr(replace_module, "search_images", lambda *_: [Candidato(
        fonte="pexels", id="blue", url="https://example.com/blue.jpg",
        miniatura="", autor="", pagina="",
    )])
    conversations = []
    turns = iter([
        {"content": "", "tool_calls": [{
            "name": "listar_elementos", "args": {"filtro": "fruta"}, "id": "call_1",
        }]},
        {"content": "", "tool_calls": [{
            "name": "trocar_imagem", "args": {
                "id": "img_001", "nova_query": "blue fruit",
            }, "id": "call_2",
        }]},
        {"content": "A imagem img_001 foi proposta para troca.", "tool_calls": []},
    ])

    def fake_turn(prompt, conversation, tools, *, settings):
        assert prompt == "chat_edicao"
        assert {tool.__name__ for tool in tools} >= {"listar_elementos", "trocar_imagem"}
        conversations.append(list(conversation))
        return next(turns)

    monkeypatch.setattr("src.editing.chat.agent.client.run_tool_turn", fake_turn)
    result = run_chat(session, "Troque a imagem da fruta por uma azul")
    assert result.acoes == [{
        "ferramenta": "trocar_imagem", "id": "img_001", "tipo": "imagem",
    }]
    assert session.plan.itens[0].query == "blue fruit"
    assert len(conversations) == 3
    assert '"ok": true' in conversations[1][-1]["content"]
    assert result.resposta.startswith("A imagem")


def test_agent_returns_validation_error_to_model_and_keeps_draft(monkeypatch):
    session = _session()
    turns = iter([
        {"content": "", "tool_calls": [{
            "name": "ajustar_duracao", "args": {"id": "img_001", "nova_duracao": 8},
            "id": "invalid",
        }]},
        {"content": "Essa duração excede o limite permitido.", "tool_calls": []},
    ])
    seen = []

    def fake_turn(_prompt, conversation, _tools, *, settings):
        seen.append(list(conversation))
        return next(turns)

    monkeypatch.setattr("src.editing.chat.agent.client.run_tool_turn", fake_turn)
    result = run_chat(session, "Deixe a imagem por oito segundos")
    assert result.acoes == []
    assert session.plan.itens[0].duracao == 2
    assert '"ok": false' in seen[1][-1]["content"]
    assert "duração" in result.resposta


def test_client_binds_pydantic_tools_and_keeps_tool_call_context(monkeypatch):
    bound = []
    seen = []

    class FakeModel:
        def bind_tools(self, tools):
            bound.extend(tools)
            return self

        def invoke(self, messages):
            seen.append(messages)
            if not any(isinstance(message, ToolMessage) for message in messages):
                return AIMessage(content="", tool_calls=[{
                    "name": "listar_elementos", "args": {"filtro": "fruta"},
                    "id": "call_1", "type": "tool_call",
                }])
            return AIMessage(content="Encontrei img_001.")

    monkeypatch.setattr(client, "_check_key", lambda *_: None)
    monkeypatch.setattr(client, "_make_model", lambda *_: FakeModel())
    conversation = [{"role": "user", "content": "Mostre a imagem da fruta"}]
    from src.editing.chat.tools import SCHEMAS

    first = client.run_tool_turn("chat_edicao", conversation, list(SCHEMAS))
    assert first["tool_calls"][0]["name"] == "listar_elementos"
    conversation.append({"role": "assistant", **first})
    conversation.append({
        "role": "tool", "tool_call_id": "call_1", "content": '{"elementos":[]}',
    })
    second = client.run_tool_turn("chat_edicao", conversation, list(SCHEMAS))
    assert second["content"] == "Encontrei img_001."
    assert len(bound) == 12
    assert any(isinstance(message, ToolMessage) for message in seen[-1])
