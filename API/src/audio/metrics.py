"""Medidas objetivas do áudio: piso de ruído, nível de fala e SNR estimado.

Servem para provar que a limpeza funcionou (Parte 1, Etapas 0 e 4) sem precisar ouvir:
o piso de ruído deve cair bem mais que o nível da fala.
"""

from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

JANELA = 0.02  # s por quadro de análise
SILENCIO_DB = -120.0  # piso usado quando o quadro é digitalmente mudo


@dataclass(frozen=True)
class Medidas:
    """Níveis em dBFS; `snr` é a distância entre a fala e o ruído de fundo."""

    fala_db: float  # percentil 90 dos quadros (a fala)
    ruido_db: float  # percentil 10 (o fundo entre as palavras)
    pico_db: float

    @property
    def snr(self) -> float:
        """Distância fala–ruído. Com `silencio_digital`, é um piso, não uma medida."""
        return self.fala_db - self.ruido_db

    @property
    def silencio_digital(self) -> bool:
        """O fundo virou zero absoluto (o DeepFilterNet faz isso entre as palavras).

        Aí `ruido_db` é a constante `SILENCIO_DB`, não um nível medido: o SNR só diz
        "não sobrou ruído mensurável", e comparar esse número com outro áudio engana.
        """
        return self.ruido_db <= SILENCIO_DB + 1e-9


def _rms_db(quadros: np.ndarray) -> np.ndarray:
    rms = np.sqrt(np.mean(np.square(quadros, dtype=np.float64), axis=1))
    return np.where(rms > 0, 20 * np.log10(np.maximum(rms, 1e-12)), SILENCIO_DB)


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """WAV PCM 16 bits → (amostras mono em -1..1, taxa)."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{Path(path).name}: só WAV PCM de 16 bits (use o ffmpeg antes)")
        canais, taxa, n = w.getnchannels(), w.getframerate(), w.getnframes()
        dados = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    if canais > 1:
        dados = dados.reshape(-1, canais).mean(axis=1)
    return dados, taxa


def measure(path: str | Path) -> Medidas:
    """Piso de ruído, nível de fala e pico de um WAV."""
    dados, taxa = read_wav(path)
    n = max(1, int(taxa * JANELA))
    sobra = len(dados) % n
    if sobra:
        dados = dados[: len(dados) - sobra]
    if len(dados) < n:
        raise ValueError(f"{Path(path).name}: áudio curto demais para medir")
    quadros = dados.reshape(-1, n)
    niveis = _rms_db(quadros)
    pico = float(np.max(np.abs(dados)))
    return Medidas(
        fala_db=float(np.percentile(niveis, 90)),
        ruido_db=float(np.percentile(niveis, 10)),
        pico_db=20 * np.log10(pico) if pico > 0 else SILENCIO_DB,
    )


def tem_audio(entrada: str | Path) -> bool:
    """O arquivo tem alguma trilha de áudio? (erro de leitura conta como "não sei": True)"""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index"]
    try:
        proc = subprocess.run([*cmd, "-of", "csv=p=0", str(entrada)], capture_output=True,
                              text=True, timeout=60)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return True  # sem ffprobe o erro aparece depois, com a mensagem do ffmpeg
    return proc.returncode != 0 or bool(proc.stdout.strip())


def to_wav(entrada: str | Path, saida: str | Path, taxa: int = 48_000, canais: int = 1) -> Path:
    """Extrai/converte qualquer entrada (vídeo ou áudio) para WAV PCM 16 bits."""
    entrada, saida = Path(entrada), Path(saida)
    if not tem_audio(entrada):
        raise RuntimeError(f"{entrada.name} não tem trilha de áudio")
    saida.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    cmd += ["-i", str(entrada), "-vn", "-ac", str(canais), "-ar", str(taxa)]
    cmd += ["-c:a", "pcm_s16le", str(saida)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg não encontrado no PATH (rode `python -m src.doctor`)") from exc
    if proc.returncode != 0 or not saida.exists():
        detalhe = (proc.stderr or "").strip().splitlines()
        motivo = detalhe[-1] if detalhe else "sem detalhe do ffmpeg"
        raise RuntimeError(f"ffmpeg falhou ao extrair o áudio de {entrada.name}: {motivo}")
    return saida
