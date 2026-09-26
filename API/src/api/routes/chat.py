"""Conversa sobre um vídeo renderizado; ações viram prévias antes da aplicação."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.api.deps import Jobs, Store, ensure_idle, ensure_project
from src.api.jobs import Job
from src.api.render_options import ensure_rendered_checkpoint, last_render_options
from src.editing.chat.history import ChatOut, load_chat
from src.editing.preview import document_sha256, preview_dir
from src.images import timeline_signature

router = APIRouter(prefix="/projects/{pid}/chat", tags=["edição"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.get("", response_model=ChatOut)
def get_chat(pid: str, store: Store) -> ChatOut:
    ensure_project(store, pid)
    project_dir = store.dir(pid)
    project = store.load(pid)
    state = load_chat(project_dir)
    pending = state.pending_token
    if pending:
        folder = preview_dir(project_dir, pending)
        if (
            state.document_sha256 != document_sha256(project)
            or not (folder / "proposal.json").is_file()
            or not (folder / "preview.mp4").is_file()
        ):
            pending = None
    messages = [item.model_copy(deep=True) for item in state.messages]
    for item in messages:
        if item.preview and item.preview.get("token") != pending:
            item.preview = None
    return ChatOut(messages=messages, pending_token=pending)


@router.post("", response_model=Job, status_code=202)
def ask_chat(pid: str, body: ChatRequest, store: Store, jobs: Jobs) -> Job:
    message = body.message.strip()
    if not message:
        raise HTTPException(422, "informe o ajuste desejado")
    with store.lock(pid):
        ensure_project(store, pid)
        ensure_idle(jobs, pid)
        project = store.load(pid)
        final = store.saida_dir(pid) / "final.mp4"
        options = last_render_options(project, jobs, pid)
        if not final.is_file() or not options:
            raise HTTPException(409, "gere o vídeo antes de conversar sobre edições")
        plan = store.load_plan(pid)
        if plan is None or plan.assinatura != timeline_signature(project):
            raise HTTPException(409, "o plano mudou; gere o vídeo antes de editar")
        if not ensure_rendered_checkpoint(store, jobs, pid, project):
            raise HTTPException(409, "há edições pendentes; gere o vídeo antes de editar")
        return jobs.submit(pid, "conversar", {
            "message": message, "render_options": options,
        })
