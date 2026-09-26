"""Loop curto de ferramentas sobre um rascunho, sem publicar o vídeo final."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field

from src.editing.chat.tools import SCHEMAS, EditSession, execute_tool
from src.llm import client

PROMPT_NAME = "chat_edicao"
MAX_TURNS = 6
MAX_ACTIONS = 4


class ChatResult(BaseModel):
    resposta: str
    acoes: list[dict[str, Any]] = Field(default_factory=list)
    conversa: list[dict[str, Any]] = Field(default_factory=list)


def run_chat(
    session: EditSession,
    request: str,
    *,
    conversation: list[dict[str, Any]] | None = None,
    max_turns: int = MAX_TURNS,
    max_actions: int = MAX_ACTIONS,
) -> ChatResult:
    """Pede ações ao modelo, executa ferramentas validadas e devolve o rascunho."""
    if not 1 <= len(request.strip()) <= 2000:
        raise ValueError("pedido deve ter de 1 a 2000 caracteres")
    history = list(conversation or [])
    history.append({"role": "user", "content": request.strip()})
    answer = quick_response(request)
    if answer:
        history.append({"role": "assistant", "content": answer, "tool_calls": []})
        return ChatResult(resposta=answer, conversa=history)
    action_count = 0
    for _ in range(max_turns):
        turn = client.run_tool_turn(
            PROMPT_NAME, history, list(SCHEMAS), settings=session.options.llm_settings()
        )
        calls = turn["tool_calls"]
        history.append({"role": "assistant", **turn})
        if not calls:
            return ChatResult(
                resposta=turn["content"], acoes=list(session.changes), conversa=history
            )
        for call in calls:
            try:
                if call["name"] != "listar_elementos" and action_count >= max_actions:
                    raise ValueError(
                        f"limite de {max_actions} edição(ões) por pedido; confirme este resultado"
                    )
                output = execute_tool(session, call["name"], call["args"])
                if "alteracao" in output:
                    action_count += 1
                payload = {"ok": True, **output}
            except (KeyError, ValueError) as exc:
                payload = {"ok": False, "erro": str(exc)}
            history.append({
                "role": "tool", "tool_call_id": call["id"],
                "content": json.dumps(payload, ensure_ascii=False, default=str),
            })
    return ChatResult(
        resposta="Limite de etapas do chat atingido; confira as ações propostas.",
        acoes=list(session.changes), conversa=history,
    )


def quick_response(request: str) -> str | None:
    """Resolve pedidos obviamente vagos ou de replanejamento sem usar LLM/Whisper."""
    normalized = "".join(
        char for char in unicodedata.normalize("NFD", request.casefold())
        if unicodedata.category(char) != "Mn"
    )
    broad_request = (
        r"\b(refaz|refazer|recria|replanej)\w*\b.*"
        r"\b(todas|todos|inteiro|completo)\b"
    )
    if re.search(broad_request, normalized):
        return (
            "Esse pedido exige replanejamento completo. Nas opções, use "
            "'Sugerir imagens e zooms' e depois 'Gerar vídeo'; este chat "
            "ajusta elementos já existentes."
        )
    vague_request = (
        r"\s*(melhora|melhore|ajusta|arruma)"
        r"(\s+(isso|essa parte|esse trecho|o video))?[.!?\s]*"
    )
    if re.fullmatch(vague_request, normalized):
        return "Qual elemento e trecho você quer ajustar? Indique a imagem, zoom ou B-roll."
    return None
