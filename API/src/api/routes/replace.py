"""Solicitações de troca pós-render por ID estável."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.api.deps import Jobs, Store, ensure_idle, ensure_project
from src.api.jobs import Job, JobStatus
from src.api.render_options import last_render_options
from src.editing.history import rendered_matches, save_rendered_checkpoint
from src.images import timeline_signature

router = APIRouter(prefix="/projects/{pid}/replace", tags=["edição"])
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 60 * 1024 * 1024


class ReplaceRequest(BaseModel):
    modo: Literal["busca", "alternativa"]
    query: str | None = Field(None, max_length=80)
    indice: int | None = Field(None, ge=0)


def _ready(store: Store, jobs: Jobs, pid: str, element_id: str) -> dict:
    ensure_project(store, pid)
    project = store.load(pid)
    options = last_render_options(project, jobs, pid)
    if not options or not (store.saida_dir(pid) / "final.mp4").is_file():
        raise HTTPException(409, "gere o vídeo antes de substituir um elemento")
    plano = store.load_plan(pid)
    if plano is None or plano.assinatura != timeline_signature(project):
        raise HTTPException(409, "o plano mudou; gere o vídeo novamente antes da troca")
    final = store.saida_dir(pid) / "final.mp4"
    checkpoint = rendered_matches(store.dir(pid), project, final)
    if checkpoint is None:
        # Projetos gerados antes da Etapa 3 não têm checkpoint. O mtime do
        # project.json detecta edições posteriores; renomear só toca info.json.
        latest = next((
            job for job in jobs.list(pid)
            if job.tipo in {"gerar", "substituir", "desfazer", "refazer"}
            and job.status == JobStatus.concluido
            and job.terminado is not None
        ), None)
        project_mtime = (store.dir(pid) / "project.json").stat().st_mtime
        checkpoint = latest is not None and project_mtime <= latest.terminado.timestamp()
        if checkpoint:
            save_rendered_checkpoint(store.dir(pid), project, final)
    if not checkpoint:
        raise HTTPException(409, "há edições pendentes no plano; gere o vídeo antes de substituir")
    if element_id.startswith("img_"):
        exists = any(f"img_{item.id:03d}" == element_id for item in plano.itens)
    elif element_id.startswith("broll_"):
        exists = any(f"broll_{item.id:03d}" == element_id for item in plano.broll)
    else:
        exists = False
    if not exists:
        raise HTTPException(404, f"elemento {element_id} não existe no vídeo")
    if element_id.startswith("img_") and not options.get("imagens"):
        raise HTTPException(
            409, "imagens estavam desligadas no último vídeo; ative e gere novamente"
        )
    if element_id.startswith("broll_") and not (
        options.get("broll") and options.get("reenquadrar")
    ):
        raise HTTPException(409, "B-roll estava desligado no último vídeo; ative e gere novamente")
    return options


@router.post("/{element_id}", response_model=Job, status_code=202)
def replace_by_choice(
    pid: str, element_id: str, body: ReplaceRequest, store: Store, jobs: Jobs
) -> Job:
    """Busca nova mídia ou escolhe alternativa, depois remonta o vídeo em job."""
    with store.lock(pid):
        ensure_idle(jobs, pid)
        options = _ready(store, jobs, pid, element_id)
        if body.modo == "busca" and not 2 <= len((body.query or "").strip()) <= 80:
            raise HTTPException(422, "informe uma busca com 2 a 80 caracteres")
        if body.modo == "alternativa" and body.indice is None:
            raise HTTPException(422, "informe o índice da alternativa")
        return jobs.submit(pid, "substituir", {
            "element_id": element_id, "modo": body.modo,
            "query": body.query, "indice": body.indice, "render_options": options,
        })


@router.post("/{element_id}/upload", response_model=Job, status_code=202)
def replace_by_upload(
    pid: str, element_id: str, file: UploadFile, store: Store, jobs: Jobs
) -> Job:
    """Guarda upload com limite de tamanho; o worker valida e prepara o efeito."""
    suffix = Path(file.filename or "").suffix.lower()
    image = element_id.startswith("img_")
    allowed = IMAGE_SUFFIXES if image else VIDEO_SUFFIXES
    if suffix not in allowed:
        raise HTTPException(422, f"formato não suportado: {suffix or 'sem extensão'}")
    limit = MAX_IMAGE_BYTES if image else MAX_VIDEO_BYTES
    with store.lock(pid):
        ensure_idle(jobs, pid)
        options = _ready(store, jobs, pid, element_id)
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
            return jobs.submit(pid, "substituir", {
                "element_id": element_id, "modo": "upload", "upload": str(dest),
                "render_options": options,
            })
        except BaseException:
            dest.unlink(missing_ok=True)
            raise


@router.get("/{element_id}/media")
def uploaded_image(pid: str, element_id: str, store: Store) -> FileResponse:
    """Miniatura local da imagem escolhida, sem expor caminhos arbitrários."""
    ensure_project(store, pid)
    plano = store.load_plan(pid)
    if plano is None:
        raise HTTPException(404, "projeto sem plano de imagens")
    item = next((i for i in plano.itens if f"img_{i.id:03d}" == element_id), None)
    if item is None or item.candidato is None or item.candidato.fonte != "upload":
        raise HTTPException(404, "imagem enviada não está selecionada")
    path = Path(item.candidato.url).resolve()
    uploads = (store.dir(pid) / "uploads" / "replacements").resolve()
    if uploads not in path.parents or not path.is_file():
        raise HTTPException(404, "imagem enviada indisponível")
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp",
    }[path.suffix.lower()]
    return FileResponse(path, media_type=media_type)
