"""Opções do último vídeo concluído, inclusive projetos criados antes da Etapa 2."""

from __future__ import annotations

from src.api.jobs import JobManager, JobStatus
from src.api.store import ProjectStore
from src.editing.history import rendered_matches, save_rendered_checkpoint
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


def ensure_rendered_checkpoint(
    store: ProjectStore, jobs: JobManager, pid: str, project: Project
) -> bool:
    """Confirma que o documento corresponde ao MP4; migra projetos antigos."""
    final = store.dir(pid) / "saida" / "final.mp4"
    checkpoint = rendered_matches(store.dir(pid), project, final)
    if checkpoint is not None:
        return checkpoint
    latest = next((
        job for job in jobs.list(pid)
        if job.tipo in {"gerar", "substituir", "desfazer", "refazer", "aplicar_edicao"}
        and job.status == JobStatus.concluido and job.terminado is not None
    ), None)
    project_mtime = (store.dir(pid) / "project.json").stat().st_mtime
    valid = latest is not None and project_mtime <= latest.terminado.timestamp()
    if valid:
        save_rendered_checkpoint(store.dir(pid), project, final)
    return valid
