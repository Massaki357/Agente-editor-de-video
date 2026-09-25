"""Histórico das edições pós-render, com desfazer/refazer em jobs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api.deps import Jobs, Store, ensure_idle, ensure_project
from src.api.jobs import Job
from src.api.render_options import last_render_options
from src.editing.history import HistoryState, history_state, navigation_target, video_sha256

router = APIRouter(prefix="/projects/{pid}/history", tags=["edição"])


@router.get("", response_model=HistoryState)
def get_history(pid: str, store: Store) -> HistoryState:
    ensure_project(store, pid)
    return history_state(store.dir(pid), store.load(pid))


def _navigate(pid: str, direction: str, store: Store, jobs: Jobs) -> Job:
    with store.lock(pid):
        ensure_project(store, pid)
        ensure_idle(jobs, pid)
        project = store.load(pid)
        if not (store.saida_dir(pid) / "final.mp4").is_file():
            raise HTTPException(409, "gere o vídeo antes de navegar no histórico")
        try:
            navigation_target(
                store.dir(pid), project, direction,
                video_sha256(store.saida_dir(pid) / "final.mp4"),
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        options = last_render_options(project, jobs, pid)
        if not options:
            raise HTTPException(409, "opções da última geração indisponíveis")
        return jobs.submit(pid, "desfazer" if direction == "undo" else "refazer", {
            "render_options": options,
        })


@router.post("/undo", response_model=Job, status_code=202)
def undo(pid: str, store: Store, jobs: Jobs) -> Job:
    return _navigate(pid, "undo", store, jobs)


@router.post("/redo", response_model=Job, status_code=202)
def redo(pid: str, store: Store, jobs: Jobs) -> Job:
    return _navigate(pid, "redo", store, jobs)
