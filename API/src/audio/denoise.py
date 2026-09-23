"""Redução de ruído com o melhor motor disponível (Parte 1, Etapa 1).

Ordem de preferência:
1. **DeepFilterNet** (binário oficial, `deepfilter.py`): rede neural, muito acima do resto;
2. **noisereduce** (spectral gating, pacote Python): sem download, resultado bom em ruído
   estacionário (ventilador, chiado);
3. **afftdn** do FFmpeg: último recurso, sempre disponível — mas atrasa o áudio em
   ~25 ms (constante, medido), o que é aceitável para lip-sync e só acontece quando os
   dois motores acima faltam.

`aggressiveness` (0 a 1) é a mesma escala para quem chama: 0 não mexe no áudio, 0,5 limpa
o suficiente para o fundo sumir sem esvaziar a sala e 1 limpa o máximo possível. No
DeepFilterNet isso vira atenuação em dB (medido com fala real: a atenuação pedida aparece
quase exata no piso de ruído e o nível da voz não muda, −0,6 dB de 6 a 100 dB); no
noisereduce vira `prop_decrease` e no afftdn, o parâmetro `nr` — números diferentes, mesma
ideia de "quanto tirar".
"""

from __future__ import annotations

import logging
import subprocess
import wave
from pathlib import Path
from typing import Literal

import numpy as np

from src.audio import deepfilter, metrics
from src.config import Settings, get_settings

log = logging.getLogger(__name__)

Motor = Literal["deepfilternet", "noisereduce", "afftdn", "nenhum"]
MOTORES: tuple[Motor, ...] = ("deepfilternet", "noisereduce", "afftdn")


class DenoiseError(RuntimeError):
    """Nenhum motor conseguiu limpar o áudio."""


# Falhas esperadas de um motor (o resto sobe: TypeError nosso não vira fallback calado).
ERROS_DE_MOTOR = (
    deepfilter.DeepFilterIndisponivel,
    DenoiseError,
    ImportError,
    OSError,
    ValueError,
    subprocess.SubprocessError,
)


def atenuacao_db(aggressiveness: float) -> float:
    """`aggressiveness` (0..1) → quanto ruído tirar, em dB (0 → 0, 0,5 → 20, 1 → 100)."""
    a = min(max(aggressiveness, 0.0), 1.0)
    return 40 * a if a <= 0.5 else 20 + (a - 0.5) * 160


def motores_disponiveis(settings: Settings | None = None) -> list[Motor]:
    """Motores que dá para usar nesta máquina, do melhor para o mais simples."""
    settings = settings or get_settings()
    disponiveis: list[Motor] = []
    if deepfilter.pode_usar(settings):  # baixa no 1º uso; pula se o download já falhou
        disponiveis.append("deepfilternet")
    try:
        import noisereduce  # noqa: F401

        disponiveis.append("noisereduce")
    except ImportError:
        pass
    disponiveis.append("afftdn")  # o FFmpeg é requisito do projeto
    return disponiveis


def denoise(
    entrada: Path,
    saida: Path,
    *,
    aggressiveness: float = 0.5,
    motor: Motor | None = None,
    settings: Settings | None = None,
) -> tuple[Path, Motor]:
    """Limpa um WAV e devolve (arquivo, motor usado). Cai para o próximo motor se falhar."""
    entrada, saida = Path(entrada), Path(saida)
    settings = settings or get_settings()
    if aggressiveness <= 0:
        log.info("aggressiveness=0: o áudio passa sem denoise.")
        return _copiar(entrada, saida), "nenhum"

    candidatos = [motor] if motor else motores_disponiveis(settings)
    erros: list[str] = []
    for escolhido in candidatos:
        try:
            _aplicar(escolhido, entrada, saida, aggressiveness, settings)
        except ERROS_DE_MOTOR as exc:  # indisponível ou quebrado: tenta o próximo
            erros.append(f"{escolhido}: {exc}")
            # com exc_info o traceback vai para o log: um bug nosso não vira "degradação
            # silenciosa" sem deixar rastro
            log.warning(
                "Denoise com %s falhou (%s); tentando o próximo motor.",
                escolhido,
                exc,
                exc_info=True,
            )
            continue
        log.info("Ruído reduzido com %s (aggressiveness=%.2f).", escolhido, aggressiveness)
        return saida, escolhido
    raise DenoiseError("nenhum motor de redução de ruído funcionou — " + " | ".join(erros))


def _aplicar(
    motor: Motor, entrada: Path, saida: Path, aggressiveness: float, settings: Settings
) -> None:
    if motor == "deepfilternet":
        deepfilter.enhance(entrada, saida, settings, atenuacao_db=atenuacao_db(aggressiveness))
    elif motor == "noisereduce":
        _noisereduce(entrada, saida, aggressiveness)
    elif motor == "afftdn":
        _afftdn(entrada, saida, aggressiveness)
    else:
        raise ValueError(f"motor desconhecido: {motor}")


def _copiar(entrada: Path, saida: Path) -> Path:
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_bytes(entrada.read_bytes())
    return saida


def _noisereduce(entrada: Path, saida: Path, aggressiveness: float) -> None:
    """Spectral gating: `prop_decrease` é a fração do ruído removida."""
    import noisereduce as nr

    dados, taxa = metrics.read_wav(entrada)
    # 0,5 → 0,8 de remoção: acima disso o spectral gating começa a "borbulhar"
    prop = min(0.95, 0.4 + 0.8 * min(max(aggressiveness, 0.0), 1.0))
    limpo = nr.reduce_noise(
        y=dados.astype(np.float32), sr=taxa, stationary=True, prop_decrease=prop
    )
    saida.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(saida), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes((np.clip(limpo, -1, 1) * 32767).astype(np.int16).tobytes())


def _afftdn(entrada: Path, saida: Path, aggressiveness: float) -> None:
    """Filtro clássico do FFmpeg; `nr` é a redução em dB (limite do filtro: 97)."""
    nr_db = min(97.0, max(0.01, atenuacao_db(aggressiveness)))
    saida.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    cmd += ["-i", str(entrada), "-af", f"afftdn=nr={nr_db:.1f}:nf=-25", "-c:a", "pcm_s16le"]
    cmd += [str(saida)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0 or not saida.exists():
        detalhe = (proc.stderr or "").strip().splitlines()
        raise DenoiseError(f"afftdn falhou: {detalhe[-1] if detalhe else 'sem detalhe'}")
