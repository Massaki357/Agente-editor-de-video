"""O que cada tipo de job faz. Tudo aqui roda na thread do worker de jobs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src import pipeline
from src.api.jobs import Job, JobContext
from src.api.store import ProjectStore
from src.cache import file_hash
from src.face import FaceParams, render_debug, track_faces
from src.images import load_plan, save_plan
from src.llm import client, pricing
from src.pipeline import PipelineOptions
from src.project import Project

log = logging.getLogger(__name__)

TIPOS = ("transcrever", "rosto", "imagens", "gerar")
VIDEO_FINAL = "final.mp4"


def debug_video_name(path: Path) -> str:
    """Vídeo de debug do rosto pelo conteúdo do clipe (estável ao reordenar)."""
    return f"rosto_{file_hash(path)[:12]}.mp4"


COBERTURA_MIN = 0.2  # abaixo disso o rastreio quase não achou rosto no clipe


def make_runner(store: ProjectStore):
    def run(job: Job, ctx: JobContext) -> dict[str, Any]:
        tarefas = {"transcrever": transcrever, "rosto": rosto, "imagens": imagens, "gerar": gerar}
        tarefa = tarefas.get(job.tipo)
        if tarefa is None:
            raise ValueError(f"tipo de job desconhecido: {job.tipo}")
        client.reset_usage()  # só há um worker: o uso medido é o deste job
        resultado = tarefa(store, job, ctx)
        uso = pricing.resumir(client.usage_totals())
        if uso is not None:
            log.info("LLM: %s", pricing.descrever(uso))
            resultado["llm"] = uso
        avisos = avisos_do_projeto(store.load(job.projeto_id))
        if avisos:
            resultado.setdefault("avisos", []).extend(avisos)
        return resultado

    return run


def avisos_do_projeto(project: Project) -> list[str]:
    """Coisas que o usuário precisa saber sobre os clipes (não são erros)."""
    avisos = []
    for i, clip in enumerate(project.timeline.clipes, start=1):
        meta = clip.meta
        if meta is None:
            continue
        nome = f"{i}. {Path(clip.arquivo).name}"
        if not meta.tem_audio:
            avisos.append(f"{nome}: sem áudio — fica sem transcrição, cortes e legendas.")
        if meta.vfr:
            avisos.append(
                f"{nome}: fps variável — o enquadramento pode ficar alguns frames defasado. "
                "Reexporte com fps fixo se notar atraso."
            )
        if meta.hdr:
            avisos.append(f"{nome}: HDR — convertido para SDR (BT.709) com tonemapping.")
    return avisos


def transcrever(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Transcreve todos os clipes (o que já está em cache sai na hora)."""
    project = store.load(job.projeto_id)
    clipes = project.timeline.clipes
    n = max(len(clipes), 1)
    resultado = []
    for i, clip in enumerate(clipes):
        ctx.step("transcrição", i / n)
        if clip.meta is not None and not clip.meta.tem_audio:
            resultado.append({"indice": i, "palavras": 0, "sem_audio": True})
            continue
        # progresso dentro do clipe: o ctx.step também cancela (lança JobCancelled)
        t = pipeline.transcribe_clip(
            Path(clip.arquivo), on_progress=lambda f, i=i: ctx.step("transcrição", (i + f) / n)
        )
        resultado.append({"indice": i, "palavras": len(t.palavras)})
    ctx.step("transcrição", 1.0)
    return {"clipes": resultado}


def rosto(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Rastreia o rosto em todos os clipes e gera um vídeo de debug por clipe."""
    project = store.load(job.projeto_id)
    clipes = project.timeline.clipes
    params = FaceParams()
    saida = store.saida_dir(job.projeto_id)
    n = max(len(clipes), 1)
    resultado, avisos = [], []
    for i, clip in enumerate(clipes):
        ctx.step("rosto", i / n)
        # metade do progresso do clipe é a detecção, metade o vídeo de debug
        track = track_faces(
            Path(clip.arquivo),
            params,
            on_progress=lambda f, i=i: ctx.step("rosto", (i + 0.5 * f) / n),
        )
        nome = debug_video_name(Path(clip.arquivo))
        render_debug(
            Path(clip.arquivo),
            saida / nome,
            track,
            params.passo,
            on_progress=lambda f, i=i: ctx.step("rosto", (i + 0.5 + 0.5 * f) / n),
        )
        resultado.append({"indice": i, "cobertura": round(track.cobertura, 3), "debug": nome})
        if track.cobertura < COBERTURA_MIN:
            avisos.append(
                f"{i + 1}. {Path(clip.arquivo).name}: rosto detectado em só "
                f"{track.cobertura:.0%} dos frames — o enquadramento vertical fica no centro e "
                "os zooms não têm onde mirar."
            )
    ctx.step("rosto", 1.0)
    return {"clipes": resultado, "avisos": avisos}


def imagens(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Preview: aplica os cortes e monta o plano criativo (imagens e zooms), sem render.

    O plano fica salvo no projeto para o usuário aprovar/trocar; o `gerar` o usa.
    """
    options = PipelineOptions.model_validate(job.opcoes or {})
    project = store.load(job.projeto_id)
    pipeline.apply_project_cuts(project, options, ctx.step)
    anterior = load_plan(store.plan_path(job.projeto_id))
    plano = pipeline.image_plan(project, options, anterior, ctx.step)
    store.save(job.projeto_id, project)
    save_plan(plano, store.plan_path(job.projeto_id))
    return {
        "itens": len(plano.itens),
        "com_foto": sum(1 for i in plano.itens if i.ativa),
        "zooms": len(plano.zooms),
    }


def gerar(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Pipeline completo: cortes, rosto, legendas, imagens (plano salvo) e render."""
    options = PipelineOptions.model_validate(job.opcoes or {})
    project = store.load(job.projeto_id)
    saida = store.saida_dir(job.projeto_id)
    plano = load_plan(store.plan_path(job.projeto_id))
    result = pipeline.render_project(project, saida / VIDEO_FINAL, options, ctx.step, plano)
    store.save(job.projeto_id, project)  # trechos e offsets calculados
    if result.plano is not None:
        save_plan(result.plano, store.plan_path(job.projeto_id))
    return {
        "avisos": list(result.avisos),
        "imagens": result.imagens,
        "zooms": result.zooms,
        "video": VIDEO_FINAL,
        "duracao_final": round(result.duracao_final, 3),
        "duracao_original": round(result.duracao_original, 3),
        "removido_pct": round(result.removido_pct, 1),
        "tempos": {k: round(v, 2) for k, v in result.tempos.items()},
    }
