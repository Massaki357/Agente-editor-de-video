"""Dependências compartilhadas pelas rotas."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from src.api.jobs import JobManager
from src.api.store import ProjectNotFound, ProjectStore


def get_store(request: Request) -> ProjectStore:
    return request.app.state.store


def get_jobs(request: Request) -> JobManager:
    return request.app.state.jobs


Store = Annotated[ProjectStore, Depends(get_store)]
Jobs = Annotated[JobManager, Depends(get_jobs)]


def ensure_project(store: ProjectStore, pid: str) -> None:
    try:
        store.info(pid)
    except ProjectNotFound:
        raise HTTPException(404, f"projeto {pid} não encontrado") from None


def ensure_idle(jobs: JobManager, pid: str) -> None:
    """Projeto com job pendente ou rodando não pode ser alterado."""
    ativo = jobs.active(pid)
    if ativo is not None:
        raise HTTPException(
            409, f"o projeto tem um job em andamento ({ativo.tipo}); espere ou cancele"
        )
