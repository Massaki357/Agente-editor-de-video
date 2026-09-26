"""Prévia de B-roll: conferir, aprovar, trocar a busca ou remover cutaways."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from src.api.deps import Jobs, Store, ensure_idle, ensure_project
from src.api.schemas import (
    BrollEdit,
    BrollItemOut,
    BrollPreviewOut,
    DefaultTransitionEdit,
    TransitionCatalogOut,
)
from src.broll.catalog import BY_ID, PRESETS
from src.broll.preview import edit_preview
from src.broll.transitions import Cutaway, TransitionConfig, validate_cutaways
from src.config import PROJECT_ROOT, get_settings
from src.images import timeline_signature
from src.render import _clip_meta, plan_segments

router = APIRouter(prefix="/projects/{pid}/broll", tags=["broll"])
PREVIEWS_DIR = PROJECT_ROOT / "assets" / "transitions"


def _validate_layout(plan, project, default, selected_id: int | None = None) -> None:
    """Valida as margens e emendas antes de persistir a configuração."""
    cuts = []
    for item in plan.broll:
        if not (item.ativo and item.aprovado) and item.id != selected_id:
            continue
        entry = (TransitionConfig(**item.transicao_entrada.model_dump())
                 if item.transicao_entrada else None)
        exit_config = (TransitionConfig(**item.transicao_saida.model_dump())
                       if item.transicao_saida else None)
        path = item.video.arquivo if item.video else PREVIEWS_DIR / "hard_cut.mp4"
        cuts.append(Cutaway(item.inicio, item.fim, path, item.clipe, entry, exit_config))
    if cuts:
        metas = [_clip_meta(clip.arquivo, clip.meta) for clip in project.timeline.clipes]
        segments = plan_segments(project.timeline, metas, 30)
        validate_cutaways(cuts, segments, 30, default, 0.25, require_files=False)


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
                transicao_entrada=item.transicao_entrada,
                transicao_saida=item.transicao_saida,
            )
        )
    return BrollPreviewOut(valido=valid, itens=items)


@router.get("", response_model=BrollPreviewOut)
def get_preview(pid: str, store: Store) -> BrollPreviewOut:
    return _out(store, pid, _plan(store, pid))


@router.get("/transitions", response_model=TransitionCatalogOut)
def transition_catalog(pid: str, store: Store) -> TransitionCatalogOut:
    ensure_project(store, pid)
    return TransitionCatalogOut(presets=[
        {**asdict(preset),
         "preview_url": f"/api/projects/{pid}/broll/transitions/{preset.id}/preview"}
        for preset in PRESETS
    ])


@router.get("/transitions/{preset_id}/preview")
def transition_preview(pid: str, preset_id: str, store: Store) -> FileResponse:
    ensure_project(store, pid)
    if preset_id not in BY_ID:
        raise HTTPException(404, "transição desconhecida")
    path = PREVIEWS_DIR / f"{preset_id}.mp4"
    if not path.is_file():
        raise HTTPException(404, "prévia de transição indisponível")
    return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")


@router.patch("/transitions/default", response_model=DefaultTransitionEdit)
def set_default_transition(
    pid: str, body: DefaultTransitionEdit, store: Store, jobs: Jobs,
) -> DefaultTransitionEdit:
    with store.lock(pid):
        ensure_idle(jobs, pid)
        ensure_project(store, pid)
        project = store.load(pid)
        plan = store.load_plan(pid)
        try:
            if plan and plan.assinatura == timeline_signature(project):
                _validate_layout(plan, project, body.preset)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        project.broll_transition = body.preset
        store.save(pid, project)
        return body


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
            transicoes = {key: getattr(body, key) for key in (
                "transicao_entrada", "transicao_saida"
            ) if key in body.model_fields_set}
            updated = edit_preview(
                plan, item_id, query=body.query, ativo=body.ativo, aprovado=body.aprovado,
                transicoes=transicoes,
            )
            if transicoes or body.aprovado is True:
                project = store.load(pid)
                _validate_layout(updated, project, project.broll_transition, item_id)
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
