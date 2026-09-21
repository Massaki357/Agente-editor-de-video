"""Pipeline completo pela linha de comando.

    python -m src.pipeline samples/ -o output/final.mp4
    python -m src.pipeline 2.mp4 1.mp4 -o output/final.mp4   # ordem dada

Etapa 3: clipes → transcrição → cortes de silêncio → render. As próximas etapas
entram aqui como passos adicionais.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from src.clips import project_from_files, project_from_folder
from src.config import get_settings
from src.cuts import CutParams, TimeMap, apply_cuts
from src.project import Project

log = logging.getLogger(__name__)


def build_project(entradas: list[Path]) -> Project:
    if len(entradas) == 1 and entradas[0].is_dir():
        return project_from_folder(entradas[0])
    return project_from_files(entradas)


def run(
    entradas: list[Path],
    output: Path,
    *,
    cortes: bool = True,
    params: CutParams | None = None,
) -> Project:
    from src.render import render_timeline

    etapas: dict[str, float] = {}
    t0 = time.perf_counter()
    project = build_project(entradas)
    if not project.timeline.clipes:
        raise SystemExit("Nenhum clipe encontrado.")
    etapas["clipes"] = time.perf_counter() - t0

    if cortes:
        t0 = time.perf_counter()
        apply_cuts(project, params or CutParams(fps=get_settings().output_fps))
        etapas["transcrição + cortes"] = time.perf_counter() - t0

    output.parent.mkdir(parents=True, exist_ok=True)
    project_path = project.salvar(output.with_suffix(".project.json"))
    log.info("Projeto salvo em %s", project_path)

    t0 = time.perf_counter()
    settings = get_settings()
    render_timeline(
        project.timeline, output, fps=settings.output_fps, sample_rate=settings.output_sample_rate
    )
    etapas["render"] = time.perf_counter() - t0

    original = sum(c.meta.duracao for c in project.timeline.clipes if c.meta)
    final = TimeMap(project.timeline).duracao
    log.info(
        "Vídeo final: %s (%.1f s de %.1f s originais, %.0f%% removido)",
        output,
        final,
        original,
        100 * (1 - final / original) if original else 0,
    )
    for nome, dur in etapas.items():
        log.info("  %-22s %6.1f s", nome, dur)
    return project


def main(argv: list[str] | None = None) -> int:
    from src.logging_setup import setup_logging

    parser = argparse.ArgumentParser(prog="python -m src.pipeline", description=__doc__)
    parser.add_argument("entradas", nargs="+", type=Path, help="pasta ou arquivos de vídeo")
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--sem-cortes", action="store_true", help="não corta silêncios")
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
    run(args.entradas, args.output, cortes=not args.sem_cortes, params=params)
    return 0


if __name__ == "__main__":
    sys.exit(main())
