"""Normalização de volume com o `loudnorm` do FFmpeg, em duas passadas (EBU R128).

Uma passada só (modo dinâmico) comprime a faixa e pode estourar picos; duas passadas
medem o arquivo inteiro e aplicam um ganho linear com os valores medidos — o volume fica
consistente entre clipes sem alterar a dinâmica da fala.

Alvo padrão: −16 LUFS com pico real em −1,5 dBTP, que é o que as redes sociais esperam
para vídeo falado.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

ALVO_LUFS = -16.0
TRUE_PEAK = -1.5
LRA = 11.0
TIMEOUT = 900


class LoudnessError(RuntimeError):
    """O FFmpeg não conseguiu medir ou normalizar o volume."""


@dataclass(frozen=True)
class Loudness:
    """Medida do `loudnorm` (LUFS integrado, pico real, faixa dinâmica e limiar)."""

    i: float
    tp: float
    lra: float
    thresh: float
    offset: float

    @classmethod
    def from_json(cls, dados: dict) -> Loudness:
        def num(chave: str, padrao: float = 0.0) -> float:
            try:
                valor = float(dados[chave])
            except (KeyError, TypeError, ValueError):
                return padrao
            return valor if valor > -100 else padrao  # "-inf" em trechos mudos

        return cls(
            i=num("input_i", -70.0),
            tp=num("input_tp", -70.0),
            lra=num("input_lra"),
            thresh=num("input_thresh", -70.0),
            offset=num("target_offset"),
        )


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError as exc:
        raise LoudnessError("ffmpeg não encontrado no PATH (rode `python -m src.doctor`)") from exc
    except subprocess.TimeoutExpired as exc:
        raise LoudnessError(f"loudnorm passou de {TIMEOUT} s") from exc


def parse_loudnorm(stderr: str) -> Loudness:
    """Extrai o JSON que o `loudnorm` imprime no fim do stderr."""
    inicio = stderr.rfind("{")
    fim = stderr.rfind("}")
    if inicio < 0 or fim < inicio:
        raise LoudnessError("loudnorm não imprimiu as medidas (saída inesperada do ffmpeg)")
    try:
        return Loudness.from_json(json.loads(stderr[inicio : fim + 1]))
    except json.JSONDecodeError as exc:
        raise LoudnessError(f"medidas do loudnorm ilegíveis: {exc}") from exc


def measure(
    entrada: Path, *, alvo_lufs: float = ALVO_LUFS, true_peak: float = TRUE_PEAK
) -> Loudness:
    """Passada 1: mede o arquivo inteiro sem gravar nada."""
    filtro = f"loudnorm=I={alvo_lufs}:TP={true_peak}:LRA={LRA}:print_format=json"
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(entrada), "-af", filtro]
    cmd += ["-f", "null", "-"]
    proc = _run(cmd)
    if proc.returncode != 0:
        detalhe = (proc.stderr or "").strip().splitlines()
        raise LoudnessError(f"loudnorm falhou ao medir {Path(entrada).name}: {detalhe[-1:]}")
    return parse_loudnorm(proc.stderr)


def normalize(
    entrada: Path,
    saida: Path,
    *,
    alvo_lufs: float = ALVO_LUFS,
    true_peak: float = TRUE_PEAK,
    medida: Loudness | None = None,
) -> Loudness:
    """Passada 2: aplica os valores medidos e grava `saida` (WAV 16 bits).

    Devolve a medida da entrada (a da saída sai de `measure(saida)`, se precisar).
    """
    entrada, saida = Path(entrada), Path(saida)
    medida = medida or measure(entrada, alvo_lufs=alvo_lufs, true_peak=true_peak)
    filtro = (
        f"loudnorm=I={alvo_lufs}:TP={true_peak}:LRA={LRA}"
        f":measured_I={medida.i}:measured_TP={medida.tp}:measured_LRA={medida.lra}"
        f":measured_thresh={medida.thresh}:offset={medida.offset}"
        ":linear=true:print_format=summary"
    )
    saida.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "info"]
    cmd += ["-i", str(entrada), "-af", filtro, "-c:a", "pcm_s16le", "-ar", "48000", str(saida)]
    proc = _run(cmd)
    if proc.returncode != 0 or not saida.exists():
        detalhe = (proc.stderr or "").strip().splitlines()
        raise LoudnessError(f"loudnorm falhou ao normalizar {entrada.name}: {detalhe[-1:]}")
    log.info("Volume normalizado: %.1f LUFS → alvo %.1f LUFS", medida.i, alvo_lufs)
    return medida
