"""O que cada tipo de job faz. Tudo aqui roda na thread do worker de jobs."""

from __future__ import annotations

import logging
import os
import tempfile
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

TIPOS = (
    "transcrever", "rosto", "imagens", "broll", "gerar", "substituir",
    "desfazer", "refazer", "previsualizar", "aplicar_edicao",
)
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
            "desfazer": navegar_historico,
            "refazer": navegar_historico,
            "previsualizar": previsualizar,
            "aplicar_edicao": aplicar_edicao,
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
    from src.editing.history import clear_history, save_rendered_checkpoint

    clear_history(store.dir(job.projeto_id))
    save_rendered_checkpoint(
        store.dir(job.projeto_id), store.load(job.projeto_id), saida / VIDEO_FINAL
    )
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
    from src.config import get_settings
    from src.editing.history import record_edit, save_rendered_checkpoint, video_sha256
    from src.editing.replace import replace_element

    pid = job.projeto_id
    args = job.opcoes or {}
    element_id = str(args["element_id"])
    project = store.load(pid)
    before = project.model_copy(deep=True)
    output = store.saida_dir(pid) / VIDEO_FINAL
    before_video_hash = video_sha256(output)
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
    if updated == plano:
        raise ValueError("essa mídia já está selecionada")
    ctx.step("substituição", 1.0)
    result = pipeline.render_project(
        project, output, options, ctx.step, updated,
        render_cache_dir=store.dir(pid) / "render_cache",
        ids_alterados={element_id}, reuse_timeline=True,
        required_element_id=element_id,
    )
    project.opcoes_ultima_geracao = options.model_dump(mode="json")
    store.save(pid, project)
    store.save_plan(pid, updated)
    record_edit(
        store.dir(pid), before, store.load(pid), {element_id},
        max_versions=get_settings().history_max_versions,
        before_video_hash=before_video_hash,
        after_video_hash=video_sha256(output),
    )
    save_rendered_checkpoint(store.dir(pid), store.load(pid), output)
    return {
        "video": VIDEO_FINAL,
        "elemento": element_id,
        "segmentos_renderizados": result.segmentos_renderizados,
        "segmentos_reutilizados": result.segmentos_reutilizados,
        "avisos": list(result.avisos),
    }


def navegar_historico(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Restaura uma versão e remonta somente os segmentos do elemento alterado."""
    from src.editing.history import (
        navigation_target,
        save_rendered_checkpoint,
        set_cursor,
        video_sha256,
    )
    from src.editing.project_schema import plano_do_documento

    pid = job.projeto_id
    project_dir = store.dir(pid)
    current = store.load(pid)
    direction = "undo" if job.tipo == "desfazer" else "redo"
    final = store.saida_dir(pid) / VIDEO_FINAL
    target, changed_ids, target_cursor, expected_hash = navigation_target(
        project_dir, current, direction, video_sha256(final)
    )
    if not changed_ids:
        raise ValueError("versão sem IDs alterados")
    options = PipelineOptions.model_validate((job.opcoes or {})["render_options"])
    plan = plano_do_documento(target.documento)
    if plan is None:
        raise ValueError("versão sem plano criativo")
    required_id = None
    if len(changed_ids) == 1:
        element_id = next(iter(changed_ids))
        if element_id.startswith("img_") and options.imagens and any(
            f"img_{item.id:03d}" == element_id and item.ativa for item in plan.itens
        ):
            required_id = element_id
        if element_id.startswith("broll_") and options.broll and any(
            f"broll_{item.id:03d}" == element_id
            and item.ativo and item.aprovado and item.video is not None
            for item in plan.broll
        ):
            required_id = element_id
    with tempfile.TemporaryDirectory(prefix="historico_", dir=final.parent) as folder:
        candidate = Path(folder) / VIDEO_FINAL
        ctx.step("histórico", 0.0)
        result = pipeline.render_project(
            target.model_copy(deep=True), candidate, options, ctx.step, plan,
            render_cache_dir=project_dir / "render_cache",
            ids_alterados=changed_ids, reuse_timeline=True,
            required_element_id=required_id,
        )
        ctx.step("histórico", 1.0)
        if video_sha256(candidate) != expected_hash:
            raise ValueError("vídeo restaurado difere da versão salva; confira a mídia de origem")
        os.replace(candidate, final)
    store.save(pid, target)
    if store.plan_path(pid).exists():
        from src.images import save_plan

        save_plan(plan, store.plan_path(pid))
    set_cursor(project_dir, target_cursor + (1 if direction == "undo" else -1), target_cursor)
    save_rendered_checkpoint(project_dir, store.load(pid), final)
    return {
        "video": VIDEO_FINAL,
        "elementos": sorted(changed_ids),
        "versao": target_cursor,
        "segmentos_renderizados": result.segmentos_renderizados,
        "segmentos_reutilizados": result.segmentos_reutilizados,
        "avisos": list(result.avisos),
    }


def previsualizar(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Prepara mudança sem publicá-la e renderiza somente seu trecho em 360×640."""
    import shutil

    from src.config import get_settings
    from src.editing.history import rendered_matches, video_sha256
    from src.editing.preview import (
        PreviewProposal,
        clean_previews,
        document_sha256,
        limit_plan,
        preview_dir,
        preview_window,
        remove_element,
        save_proposal,
    )
    from src.editing.replace import replace_element

    pid = job.projeto_id
    args = job.opcoes or {}
    project_dir = store.dir(pid)
    project = store.load(pid)
    plan = store.load_plan(pid)
    final = store.saida_dir(pid) / VIDEO_FINAL
    if plan is None or not rendered_matches(project_dir, project, final):
        raise ValueError("o projeto mudou desde a solicitação da prévia")
    element_id = str(args["element_id"])
    action = args["action"]
    ctx.step("prévia", 0.0)
    if action == "remove":
        updated = remove_element(plan, element_id)
    else:
        updated = replace_element(
            plan, element_id, args["mode"], query=args.get("query"),
            indice=args.get("index"),
            upload=Path(args["upload"]) if args.get("upload") else None,
            pid=pid,
        )
    if updated == plan:
        raise ValueError("a edição não alterou o elemento")
    options = PipelineOptions.model_validate(args["render_options"])
    window = preview_window(updated, element_id, project.timeline.duracao_total)
    preview_plan = limit_plan(updated, window)
    token = args["token"]
    folder = preview_dir(project_dir, token)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        settings = get_settings()
        width = max(2, min(360, settings.output_width) // 2 * 2)
        height = max(2, min(640, settings.output_height) // 2 * 2)
        pipeline.render_project(
            project.model_copy(deep=True), folder / "preview.mp4", options,
            ctx.step, preview_plan, render_cache_dir=folder / "cache",
            ids_alterados={element_id}, reuse_timeline=True,
            required_element_id=element_id if action == "replace" else None,
            preview_size=(width, height), preview_range=window,
            mute_preview_audio=True,
        )
        ctx.step("prévia", 1.0)
        proposal = PreviewProposal(
            token=token, element_id=element_id, action=action,
            base_sha256=video_sha256(final),
            base_document_sha256=document_sha256(project),
            plan=updated, render_options=options.model_dump(mode="json"),
            inicio=window[0], fim=window[1],
        )
        save_proposal(project_dir, proposal)
        shutil.rmtree(folder / "cache", ignore_errors=True)
        (folder / "preview.ass").unlink(missing_ok=True)
        clean_previews(project_dir)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return {
        "token": token,
        "preview_url": f"/api/projects/{pid}/editor/previews/{token}/video",
        "inicio": window[0], "fim": window[1],
        "elemento": element_id, "acao": action,
    }


def aplicar_edicao(store: ProjectStore, job: Job, ctx: JobContext) -> dict[str, Any]:
    """Aplica proposta aprovada, renderiza incrementalmente e registra histórico."""
    import shutil

    from src.config import get_settings
    from src.editing.history import (
        record_edit,
        rendered_matches,
        save_rendered_checkpoint,
        video_sha256,
    )
    from src.editing.preview import document_sha256, load_proposal, preview_dir

    pid = job.projeto_id
    project_dir = store.dir(pid)
    token = str((job.opcoes or {})["token"])
    proposal = load_proposal(project_dir, token)
    current = store.load(pid)
    final = store.saida_dir(pid) / VIDEO_FINAL
    if not rendered_matches(project_dir, current, final):
        raise ValueError("o plano mudou desde a prévia; faça outra prévia")
    if (
        video_sha256(final) != proposal.base_sha256
        or document_sha256(current) != proposal.base_document_sha256
    ):
        raise ValueError("o vídeo mudou desde a prévia; faça outra prévia")
    options = PipelineOptions.model_validate(proposal.render_options)
    with tempfile.TemporaryDirectory(prefix="aplicar_edicao_", dir=final.parent) as folder:
        candidate = Path(folder) / VIDEO_FINAL
        ctx.step("aplicação", 0.0)
        updated = current.model_copy(deep=True)
        result = pipeline.render_project(
            updated, candidate, options, ctx.step,
            proposal.plan, render_cache_dir=project_dir / "render_cache",
            ids_alterados={proposal.element_id}, reuse_timeline=True,
            required_element_id=(
                proposal.element_id if proposal.action == "replace" else None
            ),
        )
        ctx.step("aplicação", 1.0)
        before_hash = proposal.base_sha256
        after_hash = video_sha256(candidate)
        os.replace(candidate, final)
        caption = candidate.with_suffix(".ass")
        if caption.is_file():
            os.replace(caption, final.with_suffix(".ass"))
    updated.opcoes_ultima_geracao = options.model_dump(mode="json")
    store.save(pid, updated)
    store.save_plan(pid, result.plano or proposal.plan)
    record_edit(
        project_dir, current, store.load(pid), {proposal.element_id},
        max_versions=get_settings().history_max_versions,
        before_video_hash=before_hash, after_video_hash=after_hash,
    )
    save_rendered_checkpoint(project_dir, store.load(pid), final)
    shutil.rmtree(preview_dir(project_dir, token), ignore_errors=True)
    return {
        "video": VIDEO_FINAL, "elemento": proposal.element_id, "acao": proposal.action,
        "segmentos_renderizados": result.segmentos_renderizados,
        "segmentos_reutilizados": result.segmentos_reutilizados,
        "avisos": list(result.avisos),
    }
