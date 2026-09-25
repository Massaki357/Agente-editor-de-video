"""Linha do tempo editável e prévias antes de aplicar alterações."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.api.deps import Jobs, Store, ensure_idle, ensure_project
from src.api.jobs import Job
from src.api.render_options import ensure_rendered_checkpoint, last_render_options
from src.api.routes.replace import IMAGE_SUFFIXES, MAX_IMAGE_BYTES, MAX_VIDEO_BYTES, VIDEO_SUFFIXES
from src.editing.history import video_sha256
from src.editing.preview import (
    EditorOut,
    document_sha256,
    list_editor_elements,
    load_proposal,
    preview_dir,
)
from src.images import timeline_signature

router = APIRouter(prefix="/projects/{pid}/editor", tags=["edição"])


class PreviewRequest(BaseModel):
    action: Literal["remove", "replace"]
    mode: Literal["busca", "alternativa"] | None = None
    query: str | None = Field(None, max_length=80)
    index: int | None = Field(None, ge=0)


def _ready(pid: str, element_id: str, store: Store, jobs: Jobs):
    ensure_project(store, pid)
    project = store.load(pid)
    options = last_render_options(project, jobs, pid)
    final = store.dir(pid) / "saida" / "final.mp4"
    if not final.is_file() or not options:
        raise HTTPException(409, "gere o vídeo antes de editar")
    plan = store.load_plan(pid)
    if plan is None or plan.assinatura != timeline_signature(project):
        raise HTTPException(409, "o plano mudou; gere o vídeo novamente antes de editar")
    if not ensure_rendered_checkpoint(store, jobs, pid, project):
        raise HTTPException(409, "há edições pendentes no plano; gere o vídeo antes de editar")
    item = next(
        (item for item in list_editor_elements(project, options).elements if item.id == element_id),
        None,
    )
    if item is None:
        raise HTTPException(404, f"elemento {element_id} não existe")
    if not item.editavel:
        raise HTTPException(409, f"{element_id}: efeito desligado no último vídeo")
    return project, plan, options, item


@router.get("", response_model=EditorOut)
def get_editor(pid: str, store: Store, jobs: Jobs) -> EditorOut:
    ensure_project(store, pid)
    project = store.load(pid)
    options = last_render_options(project, jobs, pid)
    if not (store.dir(pid) / "saida" / "final.mp4").is_file():
        options = {}
    return list_editor_elements(project, options)


@router.post("/{element_id}/preview", response_model=Job, status_code=202)
def preview_choice(
    pid: str, element_id: str, body: PreviewRequest, store: Store, jobs: Jobs
) -> Job:
    with store.lock(pid):
        ensure_idle(jobs, pid)
        _project, _plan, options, item = _ready(pid, element_id, store, jobs)
        if body.action == "remove":
            if not item.ativo:
                raise HTTPException(422, "elemento já está removido")
        elif element_id.startswith(("img_", "broll_")):
            if body.mode == "busca" and not 2 <= len((body.query or "").strip()) <= 80:
                raise HTTPException(422, "informe uma busca com 2 a 80 caracteres")
            if body.mode == "alternativa" and body.index is None:
                raise HTTPException(422, "informe o índice da alternativa")
            if body.mode is None:
                raise HTTPException(422, "informe busca ou alternativa")
        else:
            raise HTTPException(422, "troca disponível somente para imagem ou B-roll")
        return jobs.submit(pid, "previsualizar", {
            "token": uuid4().hex, "element_id": element_id, "action": body.action,
            "mode": body.mode, "query": body.query, "index": body.index,
            "render_options": options,
        })


@router.post("/{element_id}/preview/upload", response_model=Job, status_code=202)
def preview_upload(
    pid: str, element_id: str, file: UploadFile, store: Store, jobs: Jobs
) -> Job:
    if not element_id.startswith(("img_", "broll_")):
        raise HTTPException(422, "upload disponível somente para imagem ou B-roll")
    image = element_id.startswith("img_")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (IMAGE_SUFFIXES if image else VIDEO_SUFFIXES):
        raise HTTPException(422, "formato de arquivo não suportado")
    limit = MAX_IMAGE_BYTES if image else MAX_VIDEO_BYTES
    with store.lock(pid):
        ensure_idle(jobs, pid)
        _project, _plan, options, _item = _ready(pid, element_id, store, jobs)
        uploads = store.dir(pid) / "uploads" / "replacements"
        uploads.mkdir(parents=True, exist_ok=True)
        dest = uploads / f"{uuid4().hex}{suffix}"
        total = 0
        try:
            with dest.open("wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise HTTPException(413, f"arquivo excede {limit // 1024 // 1024} MB")
                    out.write(chunk)
            if total == 0:
                raise HTTPException(422, "arquivo vazio")
            if image:
                from PIL import Image, UnidentifiedImageError

                try:
                    with Image.open(dest) as picture:
                        picture.verify()
                except (UnidentifiedImageError, OSError):
                    raise HTTPException(422, "imagem inválida") from None
            return jobs.submit(pid, "previsualizar", {
                "token": uuid4().hex, "element_id": element_id, "action": "replace",
                "mode": "upload", "upload": str(dest), "render_options": options,
            })
        except BaseException:
            dest.unlink(missing_ok=True)
            raise


@router.get("/previews/{token}/video")
def preview_video(pid: str, token: str, store: Store) -> FileResponse:
    ensure_project(store, pid)
    try:
        folder = preview_dir(store.dir(pid), token)
    except ValueError:
        raise HTTPException(404, "prévia não encontrada") from None
    path = folder / "preview.mp4"
    if not (folder / "proposal.json").is_file() or not path.is_file():
        raise HTTPException(404, "prévia não encontrada")
    return FileResponse(path, media_type="video/mp4")


@router.post("/previews/{token}/apply", response_model=Job, status_code=202)
def apply_preview(pid: str, token: str, store: Store, jobs: Jobs) -> Job:
    with store.lock(pid):
        ensure_idle(jobs, pid)
        ensure_project(store, pid)
        try:
            proposal = load_proposal(store.dir(pid), token)
        except (OSError, ValueError):
            raise HTTPException(404, "prévia não encontrada") from None
        project, _plan, _options, _item = _ready(pid, proposal.element_id, store, jobs)
        current_hash = video_sha256(store.dir(pid) / "saida" / "final.mp4")
        if current_hash != proposal.base_sha256:
            raise HTTPException(409, "o vídeo mudou desde a prévia; faça outra prévia")
        if document_sha256(project) != proposal.base_document_sha256:
            raise HTTPException(409, "o plano mudou desde a prévia; faça outra prévia")
        if proposal.plan.assinatura != timeline_signature(project):
            raise HTTPException(409, "o plano mudou desde a prévia; faça outra prévia")
        return jobs.submit(pid, "aplicar_edicao", {"token": token})
