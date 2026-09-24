"""Jobs em segundo plano: transcrever, rastrear rosto, gerar o vídeo."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.api.deps import Jobs, Store, ensure_idle, ensure_project
from src.api.jobs import Job
from src.api.schemas import JobCreate

router = APIRouter(tags=["jobs"])


@router.post("/projects/{pid}/jobs", response_model=Job, status_code=202)
def create_job(pid: str, body: JobCreate, store: Store, jobs: Jobs) -> Job:
    """Enfileira um job. Acompanhe com `GET /jobs/{id}` (status, etapa, progresso, log)."""
    ensure_project(store, pid)
    # sob o lock do projeto: uma alteração em andamento (ex.: upload) termina antes
    with store.lock(pid):
        ensure_idle(jobs, pid)
        project = store.load(pid)
        if not project.timeline.clipes:
            raise HTTPException(422, "o projeto não tem clipes")
        if body.opcoes is not None:
            opcoes = body.opcoes.model_dump()
            project.estabilizar = body.opcoes.estabilizar
            project.suavizacao_estabilizacao = body.opcoes.suavizacao_estabilizacao
            project.broll = body.opcoes.broll
            project.broll_intervalo_min = body.opcoes.broll_intervalo_min
            project.broll_transition = body.opcoes.broll_transition
            project.legendas_continuas = body.opcoes.legendas_continuas
            project.legendas_destaque = body.opcoes.legendas_destaque
            project.estilo_destaque = body.opcoes.estilo_destaque
            store.save(pid, project)
        else:
            opcoes = {
                "estabilizar": project.estabilizar,
                "suavizacao_estabilizacao": project.suavizacao_estabilizacao,
                "broll": project.broll,
                "broll_intervalo_min": project.broll_intervalo_min,
                "broll_transition": project.broll_transition,
                "legendas_continuas": project.legendas_continuas,
                "legendas_destaque": project.legendas_destaque,
                "estilo_destaque": project.estilo_destaque.model_dump(),
            }
        if body.ids_alterados:
            opcoes["ids_alterados"] = body.ids_alterados
        return jobs.submit(pid, body.tipo, opcoes)


@router.get("/jobs", response_model=list[Job])
def list_jobs(jobs: Jobs, projeto_id: str | None = None) -> list[Job]:
    return jobs.list(projeto_id)


@router.get("/jobs/{jid}", response_model=Job)
def get_job(jid: str, jobs: Jobs) -> Job:
    try:
        return jobs.get(jid)
    except KeyError:
        raise HTTPException(404, f"job {jid} não encontrado") from None


@router.post("/jobs/{jid}/cancel", response_model=Job)
def cancel_job(jid: str, jobs: Jobs) -> Job:
    try:
        return jobs.cancel(jid)
    except KeyError:
        raise HTTPException(404, f"job {jid} não encontrado") from None
