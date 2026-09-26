"""Conversa visível e contexto do modelo persistidos por projeto."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.cache import atomic_write_text


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    actions: list[dict[str, Any]] = Field(default_factory=list)
    preview: dict[str, Any] | None = None
    applied: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChatState(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    pending_token: str | None = None
    document_sha256: str | None = None
    conversation: list[dict[str, Any]] = Field(default_factory=list)


class ChatOut(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    pending_token: str | None = None


def state_path(project_dir: Path) -> Path:
    return project_dir / ".chat" / "state.json"


def load_chat(project_dir: Path) -> ChatState:
    path = state_path(project_dir)
    if not path.is_file():
        return ChatState()
    return ChatState.model_validate_json(path.read_text(encoding="utf-8"))


def save_chat(project_dir: Path, state: ChatState) -> None:
    atomic_write_text(state_path(project_dir), state.model_dump_json(indent=2))


def mark_applied(project_dir: Path, token: str, document_sha256: str) -> None:
    state = load_chat(project_dir)
    if state.pending_token != token:
        return
    state.pending_token = None
    state.document_sha256 = document_sha256
    for message in reversed(state.messages):
        if message.preview and message.preview.get("token") == token:
            message.applied = True
            message.preview = None
            break
    save_chat(project_dir, state)
