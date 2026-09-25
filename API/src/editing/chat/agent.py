"""Loop curto de ferramentas sobre um rascunho, sem publicar o vídeo final."""

from __future__ import annotations

import json
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
) -> ChatResult:
    """Pede ações ao modelo, executa ferramentas validadas e devolve o rascunho."""
    if not 1 <= len(request.strip()) <= 2000:
        raise ValueError("pedido deve ter de 1 a 2000 caracteres")
    history = list(conversation or [])
    history.append({"role": "user", "content": request.strip()})
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
                if call["name"] != "listar_elementos" and action_count >= MAX_ACTIONS:
                    raise ValueError("limite de quatro edições por pedido; confirme este resultado")
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
