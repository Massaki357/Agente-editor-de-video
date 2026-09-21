"""Pipeline completo: clipes → transcrição → cortes (silêncio e erros de fala) → render.

Usado pela API (`src.api`) e pela linha de comando:

    python -m src.pipeline ../samples/ -o ../output/final.mp4
    python -m src.pipeline 2.mp4 1.mp4 -o ../output/final.mp4   # ordem dada

As próximas etapas (reenquadramento, legendas, imagens, zooms) entram em
`render_project` como passos adicionais. Etapa 6: reenquadramento 9:16 seguindo o rosto.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from src.clips import project_from_files, project_from_folder
from src.config import get_settings
from src.cuts import CutParams, TimeMap, apply_cuts
from src.face import track_faces
from src.project import Project
from src.reframe import CameraPath, camera_path
from src.transcribe import transcribe_clip

log = logging.getLogger(__name__)

# (etapa, fração 0..1 dentro da etapa). Pode lançar exceção para cancelar.
StepCallback = Callable[[str, float], None]


class PipelineOptions(BaseModel):
    cortes: bool = True  # cortar silêncios
    cortes_fala: bool = True  # usar o LLM para cortar erros de fala
    reenquadrar: bool = True  # 9:16 (1080x1920) seguindo o rosto; False = quadro original
    min_silencio: float = CutParams().min_silencio
    margem: float = CutParams().margem
    ruido_db: float = CutParams().ruido_db

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


def render_project(
    project: Project,
    output: Path,
    options: PipelineOptions | None = None,
    on_step: StepCallback = _noop,
) -> PipelineResult:
    """Aplica os cortes no projeto (altera os trechos) e renderiza o vídeo final."""
    from src.render import render_timeline

    options = options or PipelineOptions()
    settings = get_settings()
    timeline = project.timeline
    if not timeline.clipes:
        raise ValueError("O projeto não tem clipes.")
    tempos: dict[str, float] = {}

    t0 = time.perf_counter()
    if options.cortes:
        n = len(timeline.clipes)
        apply_cuts(
            project,
            options.cut_params(),
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
    tempos["transcrição + cortes"] = time.perf_counter() - t0

    cameras: dict[int, CameraPath] | None = None
    size = None
    if options.reenquadrar:
        t0 = time.perf_counter()
        cameras = reframe_cameras(project, on_step)
        size = (settings.output_width, settings.output_height)
        tempos["rosto"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    render_timeline(
        timeline,
        output,
        size=size,
        fps=settings.output_fps,
        sample_rate=settings.output_sample_rate,
        progress=lambda feitos, total: on_step("render", feitos / total if total else 1.0),
        cameras=cameras,
    )
    tempos["render"] = time.perf_counter() - t0

    result = PipelineResult(
        video=str(output),
        duracao_final=TimeMap(timeline).duracao,
        duracao_original=sum(c.meta.duracao for c in timeline.clipes if c.meta),
        tempos=tempos,
    )
    log.info(
        "Vídeo final: %s (%.1f s de %.1f s originais, %.0f%% removido)",
        output,
        result.duracao_final,
        result.duracao_original,
        result.removido_pct,
    )
    for nome, dur in tempos.items():
        log.info("  %-22s %6.1f s", nome, dur)
    return result


def reframe_cameras(project: Project, on_step: StepCallback = _noop) -> dict[int, CameraPath]:
    """Caminho da janela 9:16 de cada clipe que tem trechos (rastreio em cache)."""
    from src.clips import probe_clip

    clipes = project.timeline.clipes
    cameras: dict[int, CameraPath] = {}
    for i, clip in enumerate(clipes):
        on_step("rosto", i / len(clipes))
        if not clip.trechos:
            continue
        meta = clip.meta or probe_clip(clip.arquivo)
        try:
            track = track_faces(Path(clip.arquivo))
        except Exception as exc:  # sem rosto não é motivo para não gerar o vídeo
            log.warning("%s: rastreio de rosto falhou (%s); janela centralizada.", clip.nome, exc)
            track = None
        cameras[i] = camera_path(track, meta.largura, meta.altura)
    on_step("rosto", 1.0)
    return cameras


def run(
    entradas: list[Path],
    output: Path,
    *,
    cortes: bool = True,
    cortes_fala: bool = True,
    reenquadrar: bool = True,
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
        params=params,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
