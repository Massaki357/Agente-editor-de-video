"""Opções do último vídeo concluído, inclusive projetos criados antes da Etapa 2."""

from __future__ import annotations

from src.api.jobs import JobManager, JobStatus
from src.pipeline import PipelineOptions
from src.project import Project


def last_render_options(project: Project, jobs: JobManager, pid: str) -> dict:
    if project.opcoes_ultima_geracao:
        return project.opcoes_ultima_geracao
    previous = next(
        (
            job for job in jobs.list(pid)
            if job.tipo == "gerar" and job.status == JobStatus.concluido
        ),
        None,
    )
    return (
        PipelineOptions.model_validate(previous.opcoes).model_dump(mode="json")
        if previous else {}
    )
