"""Prévia de B-roll: conferir, aprovar, trocar a busca ou remover cutaways."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from src.api.deps import Jobs, Store, ensure_idle, ensure_project
from src.api.schemas import BrollEdit, BrollItemOut, BrollPreviewOut
from src.broll.preview import edit_preview
from src.config import get_settings
from src.images import timeline_signature

router = APIRouter(prefix="/projects/{pid}/broll", tags=["broll"])


def _plan(store, pid):
    ensure_project(store, pid)
    plan = store.load_plan(pid)
    if plan is None:
        raise HTTPException(404, "sem prévia de B-roll (rode o job 'broll')")
    return plan


def _out(store, pid, plan) -> BrollPreviewOut:
    valid = plan.assinatura == timeline_signature(store.load(pid))
    items = []
    for item in plan.broll:
        video = item.video
        items.append(
            BrollItemOut(
                id=item.id,
                texto=item.texto,
                query=item.query,
                inicio=item.inicio,
                duracao=item.duracao_max,
                ativo=item.ativo,
                aprovado=item.aprovado,
                fonte=video.fonte if video else None,
                autor=video.autor if video else None,
                pagina=video.pagina if video else None,
                video_id=video.id if video else None,
                video_url=(
                    f"/api/projects/{pid}/broll/{item.id}/video?v={video.id}"
                    if video and valid else None
                ),
                alternativas=[
                    {
                        "indice": indice,
                        "id": candidato.get("id", ""),
                        "fonte": candidato.get("fonte", ""),
                        "pagina": candidato.get("pagina", ""),
                        "autor": candidato.get("autor", ""),
                    }
                    for indice, candidato in enumerate(item.alternativas)
                ],
            )
        )
    return BrollPreviewOut(valido=valid, itens=items)


@router.get("", response_model=BrollPreviewOut)
def get_preview(pid: str, store: Store) -> BrollPreviewOut:
    return _out(store, pid, _plan(store, pid))


@router.patch("/{item_id}", response_model=BrollPreviewOut)
def edit_cutaway(
    pid: str, item_id: int, body: BrollEdit, store: Store, jobs: Jobs
) -> BrollPreviewOut:
    """Edita a decisão salva; outra busca roda no job, sem nova chamada ao LLM."""
    with store.lock(pid):
        ensure_idle(jobs, pid)
        plan = _plan(store, pid)
        if plan.assinatura != timeline_signature(store.load(pid)):
            raise HTTPException(409, "os cortes mudaram; gere uma nova prévia de B-roll")
        try:
            updated = edit_preview(
                plan, item_id, query=body.query, ativo=body.ativo, aprovado=body.aprovado
            )
        except KeyError:
            raise HTTPException(404, f"cutaway {item_id} não existe") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        store.save_plan(pid, updated)
        return _out(store, pid, updated)


@router.get("/{item_id}/video")
def preview_video(pid: str, item_id: int, store: Store) -> FileResponse:
    plan = _plan(store, pid)
    if plan.assinatura != timeline_signature(store.load(pid)):
        raise HTTPException(409, "os cortes mudaram; gere uma nova prévia de B-roll")
    item = next((item for item in plan.broll if item.id == item_id), None)
    if item is None or item.video is None:
        raise HTTPException(404, "cutaway sem vídeo de prévia")
    path = item.video.arquivo.resolve()
    cache = (get_settings().cache_dir / "broll_video").resolve()
    if cache not in path.parents or not path.is_file():
        raise HTTPException(404, "vídeo de prévia indisponível")
    return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")
