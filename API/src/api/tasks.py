"""O que cada tipo de job faz. Tudo aqui roda na thread do worker de jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src import pipeline
from src.api.jobs import Job, JobContext
from src.api.store import ProjectStore
from src.cache import file_hash
from src.face import FaceParams, render_debug, track_faces
from src.pipeline import PipelineOptions

TIPOS = ("transcrever", "rosto", "gerar")
VIDEO_FINAL = "final.mp4"


def debug_video_name(path: Path) -> str:
    """Vídeo de debug do rosto pelo conteúdo do clipe (estável ao reordenar)."""
    return f"rosto_{file_hash(path)[:12]}.mp4"


def make_runner(store: ProjectStore):
    def run(job: Job, ctx: JobContext) -> dict[str, Any]:
        tarefa = {"transcrever": transcrever, "rosto": rosto, "gerar": gerar}.get(job.tipo)
        if tarefa is None:
            raise ValueError(f"tipo de job desconhecido: {job.tipo}")
        return tarefa(store, job, ctx)

    return run


def transcrever(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Transcreve todos os clipes (o que já está em cache sai na hora)."""
    project = store.load(job.projeto_id)
    clipes = project.timeline.clipes
    resultado = []
    for i, clip in enumerate(clipes):
        ctx.step("transcrição", i / max(len(clipes), 1))
        if clip.meta is not None and not clip.meta.tem_audio:
            resultado.append({"indice": i, "palavras": 0, "sem_audio": True})
            continue
        t = pipeline.transcribe_clip(Path(clip.arquivo))
        resultado.append({"indice": i, "palavras": len(t.palavras)})
    ctx.step("transcrição", 1.0)
    return {"clipes": resultado}


def rosto(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Rastreia o rosto em todos os clipes e gera um vídeo de debug por clipe."""
    project = store.load(job.projeto_id)
    clipes = project.timeline.clipes
    params = FaceParams()
    saida = store.saida_dir(job.projeto_id)
    resultado = []
    for i, clip in enumerate(clipes):
        ctx.step("rosto", i / max(len(clipes), 1))
        track = track_faces(Path(clip.arquivo), params)
        nome = debug_video_name(Path(clip.arquivo))
        render_debug(Path(clip.arquivo), saida / nome, track, params.passo)
        resultado.append({"indice": i, "cobertura": round(track.cobertura, 3), "debug": nome})
    ctx.step("rosto", 1.0)
    return {"clipes": resultado}


def gerar(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Pipeline completo: cortes (silêncio + LLM) e render do vídeo final."""
    options = PipelineOptions.model_validate(job.opcoes or {})
    project = store.load(job.projeto_id)
    saida = store.saida_dir(job.projeto_id)
    result = pipeline.render_project(project, saida / VIDEO_FINAL, options, ctx.step)
    store.save(job.projeto_id, project)  # trechos e offsets calculados
    return {
        "video": VIDEO_FINAL,
        "duracao_final": round(result.duracao_final, 3),
        "duracao_original": round(result.duracao_original, 3),
        "removido_pct": round(result.removido_pct, 1),
        "tempos": {k: round(v, 2) for k, v in result.tempos.items()},
    }
