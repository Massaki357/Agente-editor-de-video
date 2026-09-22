"""Pipeline completo: clipes → transcrição → cortes (silêncio e erros de fala) → render.

Usado pela API (`src.api`) e pela linha de comando:

    python -m src.pipeline ../samples/ -o ../output/final.mp4
    python -m src.pipeline 2.mp4 1.mp4 -o ../output/final.mp4   # ordem dada

Os passos de `render_project`: cortes, rosto e reenquadramento 9:16 (Etapa 6),
legendas (7), plano criativo com imagens (8) e zooms no rosto (9), render.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from src.captions import CaptionStyle, write_captions
from src.clips import project_from_files, project_from_folder
from src.config import Settings, get_settings
from src.cuts import RESTO_MAX, CutParams, TimeMap, apply_cuts, visible_words  # noqa: F401
from src.face import FaceTrack, track_faces
from src.images import ImageParams, PlanoImagens, build_overlays, plan_images, timeline_signature
from src.project import Project
from src.reframe import CameraPath, ZoomParams, camera_path, plan_zooms, zoom_curve
from src.transcribe import transcribe_clip

log = logging.getLogger(__name__)

# (etapa, fração 0..1 dentro da etapa). Pode lançar exceção para cancelar.
StepCallback = Callable[[str, float], None]


class PipelineOptions(BaseModel):
    cortes: bool = True  # cortar silêncios
    cortes_fala: bool = True  # usar o LLM para cortar erros de fala
    reenquadrar: bool = True  # 9:16 (1080x1920) seguindo o rosto; False = quadro original
    legendas: bool = True  # legendas palavra por palavra queimadas no vídeo
    estilo_legenda: CaptionStyle = CaptionStyle()
    imagens: bool = True  # imagens sobre a fala (LLM escolhe as palavras; precisa de chave)
    sticker: bool = False  # recorta o fundo das imagens (rembg; 1º uso baixa o modelo)
    parametros_imagens: ImageParams = ImageParams()
    llm_model: str | None = None  # modelo do LLM; None = o do .env (`LLM_MODEL`)
    zooms: bool = True  # zooms no rosto em momentos de ênfase (LLM); só com `reenquadrar`
    parametros_zoom: ZoomParams = ZoomParams()
    min_silencio: float = CutParams().min_silencio
    margem: float = CutParams().margem
    ruido_db: float = CutParams().ruido_db

    def llm_settings(self) -> Settings:
        """Configurações com o modelo escolhido nesta execução."""
        return get_settings().for_model(self.llm_model)

    def cut_params(self) -> CutParams:
        return CutParams(
            min_silencio=self.min_silencio,
            margem=self.margem,
            ruido_db=self.ruido_db,
            fps=get_settings().output_fps,
        )


class PipelineResult(BaseModel):
    video: str
    duracao_final: float
    duracao_original: float
    tempos: dict[str, float]
    imagens: int = 0  # imagens que entraram no vídeo
    zooms: int = 0  # zooms que entraram no vídeo
    plano: PlanoImagens | None = None  # plano usado (para salvar no projeto)

    @property
    def removido_pct(self) -> float:
        if not self.duracao_original:
            return 0.0
        return 100 * (1 - self.duracao_final / self.duracao_original)


def _noop(etapa: str, fracao: float) -> None:
    pass


def build_project(entradas: list[Path]) -> Project:
    if len(entradas) == 1 and entradas[0].is_dir():
        return project_from_folder(entradas[0])
    return project_from_files(entradas)


def transcribe_project(project: Project, on_step: StepCallback = _noop) -> None:
    """Transcreve (ou lê do cache) todos os clipes com áudio."""
    clipes = project.timeline.clipes
    for i, clip in enumerate(clipes):
        on_step("transcrição", i / len(clipes))
        if clip.meta is None or clip.meta.tem_audio:
            transcribe_clip(Path(clip.arquivo))
    on_step("transcrição", 1.0)


def apply_project_cuts(
    project: Project, options: PipelineOptions, on_step: StepCallback = _noop
) -> None:
    """Define os trechos de cada clipe (cortes de silêncio + LLM, ou o clipe inteiro)."""
    settings = get_settings()
    timeline = project.timeline
    if options.cortes:
        n = len(timeline.clipes)
        apply_cuts(
            project,
            options.cut_params(),
            settings=options.llm_settings(),
            cortes_fala=options.cortes_fala,
            # busca no módulo a cada chamada: os testes trocam o transcritor
            transcriber=lambda path, settings=None: transcribe_clip(path, settings=settings),
            on_clip=lambda i: on_step("cortes", i / n),
        )
    else:
        fps = settings.output_fps
        for i, clip in enumerate(timeline.clipes):
            if clip.meta:
                timeline.substituir_trechos(i, [(0.0, math.floor(clip.meta.duracao * fps) / fps)])
    on_step("cortes", 1.0)


def image_plan(
    project: Project,
    options: PipelineOptions,
    plano: PlanoImagens | None = None,
    on_step: StepCallback = _noop,
) -> PlanoImagens:
    """O plano salvo, se ainda vale para os trechos atuais; senão, um novo (LLM em cache)."""
    if plano is not None and plano.assinatura == timeline_signature(project):
        log.info(
            "Usando o plano criativo salvo (%d imagens, %d zooms), sem chamar o LLM.",
            len(plano.itens),
            len(plano.zooms),
        )
        return plano
    if plano is not None:
        log.info("Os trechos mudaram desde o plano criativo salvo: refazendo o plano.")
    on_step("imagens", 0.0)
    novo = plan_images(
        project,
        transcriber=lambda path: transcribe_clip(path),
        params=options.parametros_imagens,
        settings=options.llm_settings(),
    )
    on_step("imagens", 1.0)
    return novo


def render_project(
    project: Project,
    output: Path,
    options: PipelineOptions | None = None,
    on_step: StepCallback = _noop,
    plano: PlanoImagens | None = None,
) -> PipelineResult:
    """Aplica os cortes no projeto (altera os trechos) e renderiza o vídeo final.

    `plano`: plano criativo salvo (preview aprovado); reaproveitado se ainda valer.
    """
    from src.clips import probe_clip
    from src.render import render_timeline

    options = options or PipelineOptions()
    settings = get_settings()
    timeline = project.timeline
    if not timeline.clipes:
        raise ValueError("O projeto não tem clipes.")
    tempos: dict[str, float] = {}

    t0 = time.perf_counter()
    apply_project_cuts(project, options, on_step)
    tempos["transcrição + cortes"] = time.perf_counter() - t0

    cameras: dict[int, CameraPath] | None = None
    tracks: dict[int, FaceTrack | None] = {}
    usa_zoom = options.zooms and options.reenquadrar  # zoom é a janela 9:16 menor
    if options.reenquadrar:
        size = (settings.output_width, settings.output_height)
    else:  # o render usa o tamanho do 1º clipe com trechos
        primeiro = next((c for c in timeline.clipes if c.trechos), timeline.clipes[0])
        meta = primeiro.meta or probe_clip(primeiro.arquivo)
        size = (meta.largura - meta.largura % 2, meta.altura - meta.altura % 2)
    if options.reenquadrar or options.imagens:
        t0 = time.perf_counter()
        tracks = face_tracks(project, on_step)
        if options.reenquadrar:
            cameras = reframe_cameras(project, tracks, options.parametros_zoom)
        tempos["rosto"] = time.perf_counter() - t0

    output.parent.mkdir(parents=True, exist_ok=True)
    legendas = None
    estilo = options.estilo_legenda.for_output(size)
    if options.legendas:
        t0 = time.perf_counter()
        legendas = make_captions(project, output.with_suffix(".ass"), options, size, on_step)
        tempos["legendas"] = time.perf_counter() - t0

    with tempfile.TemporaryDirectory(prefix="imagens_") as tmp_imagens:
        overlays = []
        plano_usado = None
        zoom = None
        n_zooms = 0
        if options.imagens or usa_zoom:
            t0 = time.perf_counter()
            plano_usado = image_plan(project, options, plano, on_step)
            if usa_zoom and cameras:
                zooms = plan_zooms(
                    plano_usado.zoom_intervals(),
                    TimeMap(timeline).to_src,
                    cameras,
                    options.parametros_zoom,
                    settings.output_fps,
                )
                n_zooms = len(zooms)
                zoom = zoom_curve(zooms) if zooms else None
                log.info("Zooms: %d de %d do plano", n_zooms, len(plano_usado.zoom_intervals()))
            if options.imagens:
                overlays = build_overlays(
                    plano_usado,
                    size,
                    estilo.box(size) if legendas is not None else None,
                    face_boxes_fn(project, tracks, cameras, size, zoom=zoom),
                    Path(tmp_imagens),
                    sticker=options.sticker,
                )
            tempos["plano criativo"] = time.perf_counter() - t0

        t0 = time.perf_counter()
        render_timeline(
            timeline,
            output,
            size=size if options.reenquadrar else None,
            fps=settings.output_fps,
            sample_rate=settings.output_sample_rate,
            progress=lambda feitos, total: on_step("render", feitos / total if total else 1.0),
            cameras=cameras,
            legendas=legendas,
            overlays=overlays,
            zoom=zoom,
        )
        tempos["render"] = time.perf_counter() - t0

    result = PipelineResult(
        video=str(output),
        duracao_final=TimeMap(timeline).duracao,
        duracao_original=sum(c.meta.duracao for c in timeline.clipes if c.meta),
        tempos=tempos,
        imagens=len(overlays),
        zooms=n_zooms,
        plano=plano_usado,
    )
    log.info(
        "Vídeo final: %s (%.1f s de %.1f s originais, %.0f%% removido, %d imagem(ns))",
        output,
        result.duracao_final,
        result.duracao_original,
        result.removido_pct,
        result.imagens,
    )
    for nome, dur in tempos.items():
        log.info("  %-22s %6.1f s", nome, dur)
    return result


def make_captions(
    project: Project,
    path: Path,
    options: PipelineOptions,
    size: tuple[int, int],
    on_step: StepCallback = _noop,
) -> Path | None:
    """Gera o .ass com as palavras de cada clipe no tempo do vídeo final."""
    timeline = project.timeline
    tm = TimeMap(timeline)
    por_clipe = []
    for i, clip in enumerate(timeline.clipes):
        on_step("legendas", i / len(timeline.clipes))
        limite = tm.clip_bounds(i)
        if limite is None or (clip.meta is not None and not clip.meta.tem_audio):
            continue
        palavras = transcribe_clip(Path(clip.arquivo)).palavras
        por_clipe.append((visible_words(tm, i, palavras), limite))
    on_step("legendas", 1.0)
    if not any(ws for ws, _ in por_clipe):
        log.info("Sem fala transcrita: vídeo sem legendas.")
        return None
    return write_captions(por_clipe, path, options.estilo_legenda.for_output(size), size)


def face_tracks(project: Project, on_step: StepCallback = _noop) -> dict[int, FaceTrack | None]:
    """Rastreio de rosto (em cache) de cada clipe que tem trechos."""
    clipes = project.timeline.clipes
    tracks: dict[int, FaceTrack | None] = {}
    for i, clip in enumerate(clipes):
        on_step("rosto", i / len(clipes))
        if not clip.trechos:
            continue
        try:
            tracks[i] = track_faces(Path(clip.arquivo))
        except Exception as exc:  # sem rosto não é motivo para não gerar o vídeo
            log.warning("%s: rastreio de rosto falhou (%s).", clip.nome, exc)
            tracks[i] = None
    on_step("rosto", 1.0)
    return tracks


def reframe_cameras(
    project: Project,
    tracks: dict[int, FaceTrack | None] | None = None,
    zoom: ZoomParams | None = None,
) -> dict[int, CameraPath]:
    """Caminho da janela 9:16 de cada clipe que tem trechos."""
    from src.clips import probe_clip

    if tracks is None:
        tracks = face_tracks(project)
    cameras: dict[int, CameraPath] = {}
    for i, track in tracks.items():
        clip = project.timeline.clipes[i]
        meta = clip.meta or probe_clip(clip.arquivo)
        cameras[i] = camera_path(track, meta.largura, meta.altura, zoom=zoom)
    return cameras


def face_boxes_fn(
    project: Project,
    tracks: dict[int, FaceTrack | None],
    cameras: dict[int, CameraPath] | None,
    size: tuple[int, int],
    passo: float = 0.1,
    zoom: Callable[[float], float] | None = None,
):
    """(t0, t1) em t_out → caixas do rosto real na saída (para as imagens desviarem).

    Com `zoom` (Etapa 9), a caixa é a do rosto já ampliado naquele instante.
    """
    from src.clips import probe_clip

    tm = TimeMap(project.timeline)
    ow, oh = size

    def para_saida(clip: int, caixa, t_src: float, t_out: float) -> tuple[int, int, int, int]:
        if cameras is not None and clip in cameras:
            escala = zoom(t_out) if zoom is not None else 1.0
            cx, cy, w, h = cameras[clip].to_output(caixa, t_src, size, escala)
        else:  # sem reenquadrar: o quadro inteiro com letterbox no tamanho de saída
            c = project.timeline.clipes[clip]
            meta = c.meta or probe_clip(c.arquivo)
            s = min(ow / meta.largura, oh / meta.altura)
            dx, dy = (ow - meta.largura * s) / 2, (oh - meta.altura * s) / 2
            cx = dx + caixa[0] * meta.largura * s
            cy = dy + caixa[1] * meta.altura * s
            w, h = caixa[2] * meta.largura * s, caixa[3] * meta.altura * s
        return int(cx - w / 2), int(cy - h / 2), int(w), int(h)

    def caixas(t0: float, t1: float) -> list[tuple[int, int, int, int]]:
        resultado = []
        n = max(2, int((t1 - t0) / passo) + 1)
        for k in range(n):
            t = min(t0 + k * (t1 - t0) / (n - 1), tm.duracao)
            clip, t_src = tm.to_src(t)
            track = tracks.get(clip)
            caixa = track.box_at(t_src) if track is not None else None
            if caixa is not None:
                resultado.append(para_saida(clip, caixa, t_src, t))
        return resultado

    return caixas


def run(
    entradas: list[Path],
    output: Path,
    *,
    cortes: bool = True,
    cortes_fala: bool = True,
    reenquadrar: bool = True,
    legendas: bool = True,
    imagens: bool = True,
    zooms: bool = True,
    sticker: bool = False,
    params: CutParams | None = None,
) -> Project:
    """Linha de comando: monta o projeto das entradas, processa e salva o project.json."""
    project = build_project(entradas)
    if not project.timeline.clipes:
        raise SystemExit("Nenhum clipe encontrado.")
    params = params or CutParams()
    options = PipelineOptions(
        cortes=cortes,
        cortes_fala=cortes_fala,
        reenquadrar=reenquadrar,
        legendas=legendas,
        imagens=imagens,
        zooms=zooms,
        sticker=sticker,
        min_silencio=params.min_silencio,
        margem=params.margem,
        ruido_db=params.ruido_db,
    )
    render_project(project, output, options)
    log.info("Projeto salvo em %s", project.salvar(output.with_suffix(".project.json")))
    return project


def main(argv: list[str] | None = None) -> int:
    from src.logging_setup import setup_logging

    parser = argparse.ArgumentParser(prog="python -m src.pipeline", description=__doc__)
    parser.add_argument("entradas", nargs="+", type=Path, help="pasta ou arquivos de vídeo")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--sem-cortes", action="store_true", help="não corta nada")
    parser.add_argument(
        "--sem-llm", action="store_true", help="não usa o LLM para cortar erros de fala"
    )
    parser.add_argument(
        "--sem-reenquadrar", action="store_true", help="mantém o quadro original (sem 9:16)"
    )
    parser.add_argument("--sem-legendas", action="store_true", help="não queima legendas")
    parser.add_argument("--sem-imagens", action="store_true", help="não coloca imagens")
    parser.add_argument("--sem-zooms", action="store_true", help="sem zooms no rosto")
    parser.add_argument("--sticker", action="store_true", help="imagens sem fundo (rembg)")
    parser.add_argument("--min-silencio", type=float, default=CutParams().min_silencio)
    parser.add_argument("--margem", type=float, default=CutParams().margem)
    parser.add_argument("--ruido-db", type=float, default=CutParams().ruido_db)
    args = parser.parse_args(argv)

    setup_logging()
    params = CutParams(
        min_silencio=args.min_silencio,
        margem=args.margem,
        ruido_db=args.ruido_db,
        fps=get_settings().output_fps,
    )
    run(
        args.entradas,
        args.output,
        cortes=not args.sem_cortes,
        cortes_fala=not args.sem_llm,
        reenquadrar=not args.sem_reenquadrar,
        legendas=not args.sem_legendas,
        imagens=not args.sem_imagens,
        zooms=not args.sem_zooms,
        sticker=args.sticker,
        params=params,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
