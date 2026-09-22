"""Aplicação FastAPI. Documentação interativa em http://127.0.0.1:8000/docs."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.jobs import JobManager
from src.api.routes import images, jobs, projects, system
from src.api.store import ProjectStore
from src.api.tasks import make_runner
from src.config import get_settings
from src.logging_setup import setup_logging

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
    return app


app = create_app()
