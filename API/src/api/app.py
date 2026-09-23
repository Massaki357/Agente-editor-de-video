"""Aplicação FastAPI. Documentação interativa em http://127.0.0.1:8000/docs.

Com o frontend compilado (`cd frontend && npm run build`), a API também serve a
interface em `/` — aí um comando só roda tudo (Etapa 11).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from src.api.jobs import JobManager
from src.api.routes import images, jobs, projects, system
from src.api.store import ProjectStore
from src.api.tasks import make_runner
from src.config import REPO_ROOT, get_settings
from src.logging_setup import setup_logging

log = logging.getLogger(__name__)

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

# Servidor de desenvolvimento do frontend (Vite).
FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging()
        settings = get_settings()
        store = ProjectStore(settings.data_dir / "projects")
        manager = JobManager(settings.data_dir / "jobs", make_runner(store))
        app.state.store = store
        app.state.jobs = manager
        manager.start()
        yield
        manager.stop()

    app = FastAPI(
        title="Editor de Vídeos API",
        description="Clipes → vídeo vertical editado (cortes, legendas, reenquadramento...).",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=FRONTEND_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in (system.router, projects.router, images.router, jobs.router):
        app.include_router(router, prefix="/api")
    montar_frontend(app)
    return app


def montar_frontend(app: FastAPI, dist: Path | None = None) -> bool:
    """Serve `frontend/dist` em `/`, se existir. Devolve se montou."""
    dist = dist or FRONTEND_DIST
    index = dist / "index.html"
    if not index.is_file():
        log.info("Frontend não compilado (%s): a API serve só /api e /docs.", dist)
        return False

    @app.get("/", include_in_schema=False)
    def raiz() -> FileResponse:
        return FileResponse(index)

    # `html=True` faz o StaticFiles cair no index.html do próprio dist quando o
    # caminho não é um arquivo (a interface é uma página só, com estado no #hash).
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    log.info("Frontend servido em / (a partir de %s)", dist)
    return True


app = create_app()
