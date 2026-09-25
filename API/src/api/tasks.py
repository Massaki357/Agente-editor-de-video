"""O que cada tipo de job faz. Tudo aqui roda na thread do worker de jobs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src import pipeline
from src.api.jobs import Job, JobContext
from src.api.store import ProjectStore
from src.broll.planner import BrollParams, plan_broll_project
from src.broll.preview import prepare_preview
from src.cache import file_hash
from src.face import FaceParams, render_debug, track_faces
from src.images import PlanoImagens, timeline_signature
from src.llm import client, pricing
from src.pipeline import PipelineOptions
from src.project import Project

log = logging.getLogger(__name__)

TIPOS = ("transcrever", "rosto", "imagens", "broll", "gerar", "substituir")
VIDEO_FINAL = "final.mp4"


def debug_video_name(path: Path) -> str:
    """Vídeo de debug do rosto pelo conteúdo do clipe (estável ao reordenar)."""
    return f"rosto_{file_hash(path)[:12]}.mp4"


COBERTURA_MIN = 0.2  # abaixo disso o rastreio quase não achou rosto no clipe


def make_runner(store: ProjectStore):
    def run(job: Job, ctx: JobContext) -> dict[str, Any]:
        tarefas = {
            "transcrever": transcrever,
            "rosto": rosto,
            "imagens": imagens,
            "broll": broll,
            "gerar": gerar,
            "substituir": substituir,
        }
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
    from src.video.stabilize import cached_video

    options = PipelineOptions.model_validate(job.opcoes or {})
    project = store.load(job.projeto_id)
    clipes = project.timeline.clipes
    params = FaceParams()
    saida = store.saida_dir(job.projeto_id)
    n = max(len(clipes), 1)
    resultado, avisos = [], []
    for i, clip in enumerate(clipes):
        fonte = Path(clip.arquivo)
        if options.estabilizar:
            ctx.step("estabilização", i / n)

            def progresso(etapa: str, fracao: float, i: int = i) -> None:
                metade = 0.0 if etapa == "análise" else 0.5
                ctx.step("estabilização", (i + metade + 0.5 * fracao) / n)

            fonte = cached_video(fonte, options.suavizacao_estabilizacao, on_progress=progresso)
        ctx.step("rosto", i / n)
        # metade do progresso do clipe é a detecção, metade o vídeo de debug
        track = track_faces(
            fonte,
            params,
            on_progress=lambda f, i=i: ctx.step("rosto", (i + 0.5 * f) / n),
        )
        nome = debug_video_name(fonte)
        render_debug(
            fonte,
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
    anterior = store.load_plan(job.projeto_id)
    plano = pipeline.image_plan(project, options, anterior, ctx.step)
    store.save(job.projeto_id, project)
    store.save_plan(job.projeto_id, plano)
    return {
        "itens": len(plano.itens),
        "com_foto": sum(1 for i in plano.itens if i.ativa),
        "zooms": len(plano.zooms),
    }


def broll(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Planeja e prepara a prévia; ajustes posteriores reaproveitam o plano salvo."""
    options = PipelineOptions.model_validate(job.opcoes or {})
    project = store.load(job.projeto_id)
    pipeline.apply_project_cuts(project, options, ctx.step)
    anterior = store.load_plan(job.projeto_id)
    assinatura = timeline_signature(project)
    if anterior is not None and anterior.assinatura == assinatura:
        plano = anterior
    elif options.imagens or options.zooms:
        plano = pipeline.image_plan(project, options, anterior, ctx.step)
    else:
        plano = PlanoImagens(assinatura=assinatura)
    if plano.broll_intervalo_min != options.broll_intervalo_min:
        plano = plano.model_copy(deep=True)
        plano.broll = []
    if not plano.broll:
        # Imagens desativadas não devem impedir sugestões de vídeo. Mantém-se o
        # plano original para poder voltar a ligá-las sem perder as escolhas.
        planejamento = plano.model_copy(deep=True) if not options.imagens else plano
        if not options.imagens:
            planejamento.itens = []
        planejado = plan_broll_project(
            project,
            planejamento,
            transcriber=lambda path: pipeline.transcribe_clip(path),
            params=BrollParams(intervalo_min=options.broll_intervalo_min),
            settings=options.llm_settings(),
        )
        if planejamento is plano:
            plano = planejado
        else:
            plano.broll = planejado.broll
    plano.broll_intervalo_min = options.broll_intervalo_min
    ctx.step("B-roll", 0.0)
    plano = prepare_preview(plano)
    store.save(job.projeto_id, project)
    store.save_plan(job.projeto_id, plano)
    ctx.step("B-roll", 1.0)
    return {
        "itens": len(plano.broll),
        "com_video": sum(1 for item in plano.broll if item.video is not None),
        "aprovados": sum(1 for item in plano.broll if item.ativo and item.aprovado),
    }


def gerar(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Pipeline completo: cortes, rosto, legendas, imagens, B-roll e render."""
    options = PipelineOptions.model_validate(job.opcoes or {})
    project = store.load(job.projeto_id)
    saida = store.saida_dir(job.projeto_id)
    plano = store.load_plan(job.projeto_id)
    result = pipeline.render_project(
        project, saida / VIDEO_FINAL, options, ctx.step, plano,
        render_cache_dir=store.dir(job.projeto_id) / "render_cache",
        ids_alterados=set((job.opcoes or {}).get("ids_alterados", [])),
    )
    project.opcoes_ultima_geracao = options.model_dump(mode="json")
    store.save(job.projeto_id, project)  # trechos e offsets calculados
    if result.plano is not None:
        store.save_plan(job.projeto_id, result.plano)
    return {
        "avisos": list(result.avisos),
        "imagens": result.imagens,
        "zooms": result.zooms,
        "broll": result.broll,
        "segmentos_renderizados": result.segmentos_renderizados,
        "segmentos_reutilizados": result.segmentos_reutilizados,
        "video": VIDEO_FINAL,
        "duracao_final": round(result.duracao_final, 3),
        "duracao_original": round(result.duracao_original, 3),
        "removido_pct": round(result.removido_pct, 1),
        "tempos": {k: round(v, 2) for k, v in result.tempos.items()},
    }


def substituir(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Troca mídia por ID e renderiza só os segmentos afetados, sem refazer cortes."""
    from src.editing.replace import replace_element

    pid = job.projeto_id
    args = job.opcoes or {}
    element_id = str(args["element_id"])
    project = store.load(pid)
    plano = store.load_plan(pid)
    if plano is None or not args.get("render_options"):
        raise ValueError("gere o vídeo antes de substituir um elemento")
    options = PipelineOptions.model_validate(args["render_options"])
    ctx.step("substituição", 0.0)
    updated = replace_element(
        plano, element_id, args["modo"], query=args.get("query"),
        indice=args.get("indice"),
        upload=Path(args["upload"]) if args.get("upload") else None,
        pid=pid,
    )
    ctx.step("substituição", 1.0)
    output = store.saida_dir(pid) / VIDEO_FINAL
    result = pipeline.render_project(
        project, output, options, ctx.step, updated,
        render_cache_dir=store.dir(pid) / "render_cache",
        ids_alterados={element_id}, reuse_timeline=True,
        required_element_id=element_id,
    )
    project.opcoes_ultima_geracao = options.model_dump(mode="json")
    store.save(pid, project)
    store.save_plan(pid, updated)
    return {
        "video": VIDEO_FINAL,
        "elemento": element_id,
        "segmentos_renderizados": result.segmentos_renderizados,
        "segmentos_reutilizados": result.segmentos_reutilizados,
        "avisos": list(result.avisos),
    }
