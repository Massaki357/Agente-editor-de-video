"""Cadeia de limpeza do áudio: highpass → denoise → loudnorm (Parte 1, Etapa 1).

    from src.audio.optimize import optimize_audio
    resultado = optimize_audio("aula.mp4", "aula_limpo.wav")

Cada passo tem um porquê:
1. **highpass 80 Hz** tira rumble, passo, vento e o offset de DC — barato e sempre ajuda
   em voz; abaixo disso não há fala útil.
2. **denoise** (`denoise.py`) escolhe o melhor motor disponível; `aggressiveness` controla
   o quanto tirar, para não robotizar áudio que já estava limpo.
3. **loudnorm em duas passadas** (`loudness.py`) deixa todos os clipes no mesmo volume.

O resultado fica em cache por hash do arquivo + parâmetros + versão da cadeia: mudar
qualquer parâmetro invalida o cache. A saída é sempre WAV 48 kHz mono de 16 bits, que é o
que o Whisper e o render esperam.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from src.audio import denoise as denoise_mod
from src.audio import loudness as loudness_mod
from src.audio import metrics
from src.cache import atomic_write_text, cache_path, file_hash
from src.config import Settings, get_settings

log = logging.getLogger(__name__)

CADEIA_VERSION = 1  # mudou a cadeia? suba isto para invalidar o cache


class AudioParams(BaseModel):
    """Parâmetros da limpeza (entram na chave do cache)."""

    aggressiveness: float = Field(0.5, ge=0, le=1, description="0 não limpa, 1 limpa ao máximo")
    alvo_lufs: float = Field(-16.0, le=0, description="volume alvo (padrão das redes sociais)")
    true_peak: float = Field(-1.5, le=0, description="pico real máximo, em dBTP")
    highpass_hz: int = Field(80, ge=0, le=300, description="corta abaixo disso (0 desliga)")
    normalizar: bool = Field(True, description="aplicar o loudnorm no fim")
    motor: denoise_mod.Motor | None = Field(
        None, description="forçar um motor de denoise (padrão: o melhor disponível)"
    )


@dataclass(frozen=True)
class ResultadoAudio:
    """O que a cadeia produziu, com o antes e o depois medidos."""

    caminho: Path
    motor: str
    antes: metrics.Medidas
    depois: metrics.Medidas
    lufs_antes: float
    lufs_depois: float
    do_cache: bool = False

    @property
    def ruido_removido_db(self) -> float:
        """Queda do piso de ruído em dBFS — **só faz sentido com `normalizar=False`**.

        Com o loudnorm ligado, o ganho aplicado no fim levanta o ruído junto com a voz e
        este número fica pequeno mesmo numa limpeza forte. O número honesto é o SNR
        (`ganho_snr_db`), que é o que o log e a interface mostram.
        """
        return self.antes.ruido_db - self.depois.ruido_db

    @property
    def ganho_snr_db(self) -> float:
        return self.depois.snr - self.antes.snr


def motor_efetivo(params: AudioParams, settings: Settings | None = None) -> str:
    """Motor que a cadeia usaria agora (o forçado, ou o melhor disponível)."""
    if params.aggressiveness <= 0:
        return "nenhum"
    if params.motor:
        return params.motor
    return denoise_mod.motores_disponiveis(settings)[0]


def cache_key(entrada: Path, params: AudioParams) -> str:
    """Chave do cache: versão da cadeia + conteúdo do arquivo + parâmetros."""
    dados = [CADEIA_VERSION, file_hash(entrada), params.model_dump()]
    return hashlib.sha256(json.dumps(dados, sort_keys=True).encode()).hexdigest()[:24]


def _posicao(motor: str) -> int:
    """Ordem de qualidade (0 é o melhor); motor desconhecido ou "nenhum" fica por último."""
    try:
        return denoise_mod.MOTORES.index(motor)  # type: ignore[arg-type]
    except ValueError:
        return len(denoise_mod.MOTORES)


def cache_desatualizado(motor_usado: str, params: AudioParams, settings: Settings | None) -> bool:
    """O cache foi feito com um motor pior do que o que dá para usar agora?

    O caso real: o binário do DeepFilterNet é baixado no 1º uso (ou o download falhou por
    falta de rede), a limpeza caiu no fallback e ficou no cache. Sem esta checagem, todas
    as execuções seguintes devolveriam o áudio pior para sempre.
    """
    return _posicao(motor_usado) > _posicao(motor_efetivo(params, settings))


def optimize_audio(
    entrada: str | Path,
    saida: str | Path,
    params: AudioParams | float | None = None,
    *,
    settings: Settings | None = None,
    use_cache: bool = True,
) -> ResultadoAudio:
    """Limpa o áudio de `entrada` (vídeo ou áudio) e grava o WAV em `saida`.

    `params` aceita o objeto completo ou só o `aggressiveness`:
    `optimize_audio(a, b, 0.7)` é o mesmo que `AudioParams(aggressiveness=0.7)`.
    """
    entrada, saida = Path(entrada), Path(saida)
    if not entrada.is_file():
        raise FileNotFoundError(f"arquivo não encontrado: {entrada}")
    if isinstance(params, int | float):
        params = AudioParams(aggressiveness=float(params))
    params = params or AudioParams()
    settings = settings or get_settings()

    chave = cache_key(entrada, params)
    wav_cache = cache_path("audio_limpo", chave, ".wav", cache_dir=settings.cache_dir)
    meta_cache = cache_path("audio_limpo", chave, ".json", cache_dir=settings.cache_dir)
    if use_cache and wav_cache.is_file() and meta_cache.is_file():
        try:
            resultado = _do_cache(wav_cache, meta_cache, saida)
        except (OSError, ValueError, KeyError) as exc:
            log.warning("Cache de áudio ignorado (%s): %s", meta_cache, exc)
        else:
            if cache_desatualizado(resultado.motor, params, settings):
                log.info(
                    "O cache de %s foi feito com %s; agora dá para usar %s — refazendo.",
                    entrada.name,
                    resultado.motor,
                    motor_efetivo(params, settings),
                )
            else:
                log.info("Áudio limpo veio do cache (%s).", chave)
                return resultado

    with tempfile.TemporaryDirectory(prefix="audio_opt_") as tmp:
        tmp = Path(tmp)
        original = metrics.to_wav(entrada, tmp / "1_original.wav")
        antes = metrics.measure(original)
        lufs_antes = loudness_mod.measure(original, alvo_lufs=params.alvo_lufs).i

        atual = original
        if params.highpass_hz > 0:
            atual = _highpass(atual, tmp / "2_highpass.wav", params.highpass_hz)
        atual, motor = denoise_mod.denoise(
            atual,
            tmp / "3_limpo.wav",
            aggressiveness=params.aggressiveness,
            motor=params.motor,  # type: ignore[arg-type]
            settings=settings,
        )
        if params.normalizar:
            loudness_mod.normalize(
                atual,
                tmp / "4_normalizado.wav",
                alvo_lufs=params.alvo_lufs,
                true_peak=params.true_peak,
            )
            atual = tmp / "4_normalizado.wav"

        depois = metrics.measure(atual)
        lufs_depois = loudness_mod.measure(atual, alvo_lufs=params.alvo_lufs).i
        saida.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(atual, saida)
        if use_cache:
            _guardar(atual, wav_cache, meta_cache, motor, antes, depois, lufs_antes, lufs_depois)

    log.info(
        "Áudio de %s: SNR %.1f → %.1f dB (+%.1f), volume %.1f → %.1f LUFS (motor %s)",
        entrada.name,
        antes.snr,
        depois.snr,
        depois.snr - antes.snr,
        lufs_antes,
        lufs_depois,
        motor,
    )
    return ResultadoAudio(saida, motor, antes, depois, lufs_antes, lufs_depois)


def _highpass(entrada: Path, saida: Path, corte_hz: int) -> Path:
    """Tira rumble e offset de DC antes de qualquer outra coisa."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    cmd += ["-i", str(entrada), "-af", f"highpass=f={corte_hz}", "-c:a", "pcm_s16le", str(saida)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not saida.exists():
        detalhe = (proc.stderr or "").strip().splitlines()
        motivo = detalhe[-1] if detalhe else "sem detalhe do ffmpeg"
        raise RuntimeError(f"highpass falhou em {entrada.name}: {motivo}")
    return saida


def _medidas(dados: dict) -> metrics.Medidas:
    return metrics.Medidas(dados["fala_db"], dados["ruido_db"], dados["pico_db"])


def _do_cache(wav_cache: Path, meta_cache: Path, saida: Path) -> ResultadoAudio:
    meta = json.loads(meta_cache.read_text(encoding="utf-8"))
    saida.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(wav_cache, saida)
    return ResultadoAudio(
        caminho=saida,
        motor=meta["motor"],
        antes=_medidas(meta["antes"]),
        depois=_medidas(meta["depois"]),
        lufs_antes=meta["lufs_antes"],
        lufs_depois=meta["lufs_depois"],
        do_cache=True,
    )


def _guardar(
    wav: Path,
    wav_cache: Path,
    meta_cache: Path,
    motor: str,
    antes: metrics.Medidas,
    depois: metrics.Medidas,
    lufs_antes: float,
    lufs_depois: float,
) -> None:
    wav_cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = wav_cache.with_suffix(".wav.part")
    shutil.copyfile(wav, tmp)
    tmp.replace(wav_cache)
    meta = {
        "motor": motor,
        "antes": antes.__dict__,
        "depois": depois.__dict__,
        "lufs_antes": lufs_antes,
        "lufs_depois": lufs_depois,
    }
    atomic_write_text(meta_cache, json.dumps(meta, indent=2))
