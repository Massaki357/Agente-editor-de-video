"""CLI do estabilizador: `python -m src.video.stabilize entrada.mp4 saida.mp4`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.logging_setup import setup_logging
from src.video.stabilize import selecionar_metodo, stabilize_video

LARGURA_BARRA = 28
NOMES = {"análise": "Passada 1/2: analisando", "estabilização": "Passada 2/2: estabilizando"}


class Barra:
    """Barra no terminal; uma linha por passada quando a saída é redirecionada."""

    def __init__(self, saida=None):
        self.saida = saida if saida is not None else sys.stderr
        self.terminal = bool(getattr(self.saida, "isatty", lambda: False)())
        self.etapa_anterior = ""
        self.percentual_anterior = -1

    def __call__(self, etapa: str, fracao: float) -> None:
        if etapa == "cache":
            print("Resultado reutilizado do cache.", file=self.saida, flush=True)
            return
        nome = NOMES.get(etapa, etapa)
        percentual = round(min(max(fracao, 0.0), 1.0) * 100)
        if not self.terminal:
            if etapa != self.etapa_anterior:
                print(f"{nome}...", file=self.saida, flush=True)
                self.etapa_anterior = etapa
            return
        if etapa != self.etapa_anterior and self.etapa_anterior:
            print(file=self.saida, flush=True)
        if etapa != self.etapa_anterior or percentual != self.percentual_anterior:
            cheio = round(LARGURA_BARRA * percentual / 100)
            barra = "#" * cheio + "-" * (LARGURA_BARRA - cheio)
            print(f"\r{nome} [{barra}] {percentual:3d}%", end="", file=self.saida, flush=True)
        self.etapa_anterior = etapa
        self.percentual_anterior = percentual
        if percentual == 100:
            print(file=self.saida, flush=True)
            self.etapa_anterior = ""


def main(argv: list[str] | None = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):  # pipes no Windows usam cp1252 por padrão
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="python -m src.video.stabilize",
        description="Estabiliza um vídeo MP4 com vidstab ou OpenCV em duas passadas.",
    )
    parser.add_argument("entrada", type=Path, help="vídeo de entrada")
    parser.add_argument("saida", type=Path, help="vídeo estabilizado (.mp4)")
    parser.add_argument(
        "--smoothing",
        choices=("leve", "medio", "forte"),
        default=None,
        help="suavização da câmera (padrão: STABILIZE_SMOOTHING do .env, ou medio)",
    )
    parser.add_argument(
        "--crop",
        type=float,
        default=None,
        metavar="PERCENTUAL",
        help="zoom adicional de 0 a 30%% (padrão: STABILIZE_CROP_PERCENT ou automático)",
    )
    parser.add_argument("--sem-cache", action="store_true", help="refaz as duas passadas")
    parser.add_argument(
        "--metodo",
        choices=("auto", "vidstab", "opencv"),
        default="auto",
        help="motor da estabilização (padrão: auto, com fallback OpenCV)",
    )
    args = parser.parse_args(argv)
    setup_logging(level="WARNING")

    try:
        motor = selecionar_metodo(args.metodo)
        print(f"Método: {motor}" + (" (fallback)" if motor == "opencv" else ""), file=sys.stderr)
        saida = stabilize_video(
            args.entrada,
            args.saida,
            smoothing=args.smoothing,
            crop_percent=args.crop,
            use_cache=not args.sem_cache,
            on_progress=Barra(),
            metodo=motor,
        )
    except KeyboardInterrupt:
        print("\nEstabilização cancelada.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    print(f"Vídeo estabilizado: {saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
