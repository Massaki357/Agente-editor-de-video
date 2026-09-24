"""Preview do plano criativo (imagens e zooms): ver e ajustar antes do render."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api.deps import Jobs, Store, ensure_idle, ensure_project
from src.api.schemas import ImagemEdit, PlanoOut, ZoomEdit
from src.images import search_images, timeline_signature

router = APIRouter(prefix="/projects/{pid}/imagens", tags=["imagens"])


def _plano(store, pid: str):
    ensure_project(store, pid)
    plano = store.load_plan(pid)
    if plano is None:
        raise HTTPException(404, "sem plano criativo (rode o job 'imagens')")
    return plano


@router.get("", response_model=PlanoOut)
def get_plan(pid: str, store: Store) -> PlanoOut:
    """O plano salvo. `valido=False` se os cortes mudaram (o `gerar` refaz o plano)."""
    plano = _plano(store, pid)
    valido = plano.assinatura == timeline_signature(store.load(pid))
    return PlanoOut(valido=valido, plano=plano)


@router.patch("/{item_id}", response_model=PlanoOut)
def edit_item(pid: str, item_id: int, body: ImagemEdit, store: Store, jobs: Jobs) -> PlanoOut:
    """Troca a foto, liga/desliga o item ou busca de novo com outra query (sem LLM)."""
    with store.lock(pid):
        ensure_idle(jobs, pid)
        plano = _plano(store, pid)
        item = next((i for i in plano.itens if i.id == item_id), None)
        if item is None:
            raise HTTPException(404, f"item {item_id} não existe no plano")
        if body.query is not None and body.query.strip() != item.query:
            candidatos = search_images(body.query.strip())
            if not candidatos:
                raise HTTPException(422, f"nenhuma foto encontrada para '{body.query}'")
            sem_foto_antes = not item.candidatos
            item.query, item.candidatos, item.escolhida = body.query.strip(), candidatos, 0
            if sem_foto_antes:  # estava desligado só por falta de foto: liga
                item.ativa = True  # (se o usuário tinha desligado, continua desligado)
        if body.escolhida is not None:
            if body.escolhida >= len(item.candidatos):
                raise HTTPException(422, f"o item tem {len(item.candidatos)} candidato(s)")
            item.escolhida = body.escolhida
        if body.ativa is not None:
            if body.ativa and not item.candidatos:
                raise HTTPException(422, "o item não tem foto; mude a busca")
            item.ativa = body.ativa
        store.save_plan(pid, plano)
        valido = plano.assinatura == timeline_signature(store.load(pid))
        return PlanoOut(valido=valido, plano=plano)


@router.patch("/zooms/{zoom_id}", response_model=PlanoOut)
def edit_zoom(pid: str, zoom_id: int, body: ZoomEdit, store: Store, jobs: Jobs) -> PlanoOut:
    """Liga/desliga um zoom no rosto do plano (sem LLM)."""
    with store.lock(pid):
        ensure_idle(jobs, pid)
        plano = _plano(store, pid)
        zoom = next((z for z in plano.zooms if z.id == zoom_id), None)
        if zoom is None:
            raise HTTPException(404, f"zoom {zoom_id} não existe no plano")
        zoom.ativo = body.ativo
        store.save_plan(pid, plano)
        valido = plano.assinatura == timeline_signature(store.load(pid))
        return PlanoOut(valido=valido, plano=plano)
