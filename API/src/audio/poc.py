"""Prova de conceito do otimizador de áudio (Parte 1, Etapa 0).

    python -m src.audio.poc ../samples/aula.mp4 ../output/limpo.wav [--atenuacao 100]

Extrai o áudio, roda o DeepFilterNet e mostra o antes/depois (piso de ruído, nível da
fala e SNR). Ainda não é a cadeia completa da Etapa 1 (falta o highpass e o loudnorm)
nem está ligado ao pipeline de vídeo.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from src.audio import deepfilter, metrics
from src.logging_setup import setup_logging


def run(entrada: Path, saida: Path, atenuacao_db: float = 100.0) -> tuple[metrics.Medidas, ...]:
    """Limpa `entrada` em `saida` (WAV) e devolve as medidas antes e depois."""
    with tempfile.TemporaryDirectory(prefix="audio_poc_") as tmp:
        wav = metrics.to_wav(entrada, Path(tmp) / "original.wav")
        antes = metrics.measure(wav)
        deepfilter.enhance(wav, saida, atenuacao_db=atenuacao_db)
    return antes, metrics.measure(saida)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.audio.poc", description=__doc__)
    parser.add_argument("entrada", type=Path, help="vídeo ou áudio de entrada")
    parser.add_argument("saida", type=Path, help="WAV limpo de saída")
    parser.add_argument(
        "--atenuacao",
        type=float,
        default=100.0,
        help="limite de atenuação do ruído em dB (100 = limpeza total, 0 = nada)",
    )
    args = parser.parse_args(argv)

    setup_logging()
    if hasattr(sys.stdout, "reconfigure"):  # pipes no Windows usam cp1252 por padrão
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not args.entrada.is_file():
        print(f"arquivo não encontrado: {args.entrada}", file=sys.stderr)
        return 1
    try:
        antes, depois = run(args.entrada, args.saida, args.atenuacao)
    except (deepfilter.DeepFilterIndisponivel, RuntimeError, ValueError) as exc:
        print(f"falhou: {exc}", file=sys.stderr)
        return 1

    print(f"{'':<10}{'fala':>10}{'ruído':>10}{'SNR':>10}{'pico':>10}")
    for nome, m in (("antes", antes), ("depois", depois)):
        print(f"{nome:<10}{m.fala_db:>9.1f}{m.ruido_db:>10.1f}{m.snr:>10.1f}{m.pico_db:>10.1f}")
    print(f"\nruído caiu {antes.ruido_db - depois.ruido_db:.1f} dB; ", end="")
    print(f"SNR subiu {depois.snr - antes.snr:.1f} dB → {args.saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
