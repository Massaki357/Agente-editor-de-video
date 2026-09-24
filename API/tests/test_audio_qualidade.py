"""Parte 1, Etapa 4: quanto a cadeia melhora o áudio, medido em SNR e LUFS.

Três ruídos diferentes, porque cada um estressa um pedaço da cadeia:
- **chiado** (branco, banda toda): o caso clássico do denoise;
- **ambiente** (ar-condicionado: grave contínuo + zumbido de 120 Hz): pega o highpass;
- **eco leve** (reflexão curta): o denoise quase não ajuda, e o teste **mede o eco
  residual** (autocorrelação no atraso injetado) para provar isso, em vez de confiar no
  SNR — que no eco fica inflado pelo silêncio digital do DeepFilterNet.

A fala é a do `samples/`, então estes testes rodam com `-m integration`. Os padrões
(`AUDIO_AGGRESSIVENESS`, `AUDIO_TARGET_LUFS`) têm teste rápido no fim do arquivo.
"""

import wave
from pathlib import Path

import numpy as np
import pytest

from src.audio import loudness, metrics
from src.audio.optimize import AudioParams, optimize_audio
from src.config import SAMPLES_DIR, Settings, get_settings

from .conftest import requires_ffmpeg

# Ganho mínimo de SNR (dB) por motor e tipo de ruído, com margem sobre o medido nesta
# etapa: deepfilternet 18,0/18,9 · noisereduce 6,3/9,9 · afftdn 18,3/5,2. Cada motor
# limpa de um jeito, então exigir o número do DeepFilterNet de todos seria irreal.
GANHO_MINIMO = {
    "deepfilternet": {"chiado": 12.0, "ambiente": 10.0},
    "noisereduce": {"chiado": 4.0, "ambiente": 6.0},
    "afftdn": {"chiado": 12.0, "ambiente": 3.0},
}
TOLERANCIA_LUFS = 1.5
ATRASO_ECO = 0.045  # s de atraso da reflexão injetada no caso "eco"


def S(**kw) -> Settings:
    kw.setdefault("cache_dir", get_settings().cache_dir)
    return Settings(**kw)


def _grava(path: Path, sinal: np.ndarray, taxa: int) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes((np.clip(sinal, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


def _com_ruido(tipo: str, voz: np.ndarray, taxa: int) -> np.ndarray:
    rng = np.random.default_rng(17)
    t = np.arange(len(voz)) / taxa
    if tipo == "chiado":
        return 0.6 * voz + rng.normal(0, 10 ** (-34 / 20), len(voz))
    if tipo == "ambiente":  # ar-condicionado: grave contínuo + zumbido da rede
        grave = rng.normal(0, 10 ** (-30 / 20), len(voz))
        grave = np.convolve(grave, np.ones(200) / 200, mode="same")  # só as baixas
        return 0.6 * voz + grave + 0.01 * np.sin(2 * np.pi * 120 * t)
    if tipo == "eco":  # reflexão curta de parede
        atraso = int(ATRASO_ECO * taxa)
        eco = np.zeros_like(voz)
        eco[atraso:] = 0.35 * voz[:-atraso]
        return 0.6 * (voz + eco)
    raise ValueError(tipo)


@pytest.fixture(scope="module")
def fala(tmp_path_factory) -> tuple[np.ndarray, int]:
    """20 s de fala real do `samples/` (pulado se não houver vídeo lá)."""
    fontes = sorted(p for p in SAMPLES_DIR.glob("*.mp4"))
    if not fontes:
        pytest.skip("coloque um vídeo com voz em samples/ (veja testes-pendentes.md)")
    wav = metrics.to_wav(fontes[0], tmp_path_factory.mktemp("fala") / "voz.wav")
    dados, taxa = metrics.read_wav(wav)
    return dados[: taxa * 20], taxa


def _eco_residual(caminho: Path, taxa: int) -> float:
    """Autocorrelação normalizada no atraso do eco: quanto da reflexão continua lá."""
    dados, _ = metrics.read_wav(caminho)
    atraso = int(ATRASO_ECO * taxa)
    a, b = dados[:-atraso], dados[atraso:]
    energia = float(np.sqrt(np.sum(a**2) * np.sum(b**2)))
    return float(np.sum(a * b) / energia) if energia > 0 else 0.0


@pytest.mark.integration
@requires_ffmpeg
@pytest.mark.parametrize("tipo", ["chiado", "ambiente"])
def test_snr_and_loudness_after_cleaning(tipo, fala, tmp_path):
    voz, taxa = fala
    entrada = _grava(tmp_path / f"{tipo}.wav", _com_ruido(tipo, voz, taxa), taxa)
    r = optimize_audio(entrada, tmp_path / f"{tipo}_limpo.wav", settings=S(cache_dir=tmp_path))

    minimo = GANHO_MINIMO.get(r.motor, {}).get(tipo)
    assert minimo is not None, f"motor sem limiar medido: {r.motor}"
    assert r.ganho_snr_db >= minimo, (
        f"{tipo} com {r.motor}: SNR {r.antes.snr:.1f} → {r.depois.snr:.1f} dB "
        f"(esperado +{minimo:.0f} dB)"
    )
    assert r.lufs_depois == pytest.approx(-16, abs=TOLERANCIA_LUFS)
    assert r.depois.pico_db <= -1.0  # o pico respeita o teto do loudnorm


@pytest.mark.integration
@requires_ffmpeg
def test_echo_is_not_removed_by_the_cleaning(fala, tmp_path):
    """A cadeia tira ruído, não eco — e é isso que o teste prova.

    O SNR não serve aqui: o DeepFilterNet zera os trechos entre as palavras, o piso vira
    a constante de silêncio digital e o "ganho" fica inflado. O que mede eco é a
    autocorrelação no atraso da reflexão.
    """
    voz, taxa = fala
    entrada = _grava(tmp_path / "eco.wav", _com_ruido("eco", voz, taxa), taxa)
    limpa = _grava(tmp_path / "sem_eco.wav", voz, taxa)
    r = optimize_audio(entrada, tmp_path / "eco_limpo.wav", settings=S(cache_dir=tmp_path))

    sem_eco = _eco_residual(limpa, taxa)
    com_eco = _eco_residual(entrada, taxa)
    depois = _eco_residual(r.caminho, taxa)
    assert com_eco > sem_eco + 0.2  # o eco injetado aparece na medida
    assert depois > com_eco - 0.1  # e continua lá depois da limpeza
    assert r.lufs_depois == pytest.approx(-16, abs=TOLERANCIA_LUFS)  # o volume, sim, é ajustado
    # o SNR do eco não vale como qualidade: o fundo virou silêncio digital
    assert r.depois.silencio_digital


@pytest.mark.integration
@requires_ffmpeg
def test_clean_speech_is_not_damaged(fala, tmp_path):
    """Áudio que já estava bom não pode piorar: volume no alvo e fala preservada."""
    voz, taxa = fala
    entrada = _grava(tmp_path / "limpa.wav", voz, taxa)
    antes = metrics.measure(entrada)
    r = optimize_audio(entrada, tmp_path / "saida.wav", settings=S(cache_dir=tmp_path))

    assert r.depois.snr >= antes.snr  # nunca fica mais ruidoso
    assert r.lufs_depois == pytest.approx(-16, abs=TOLERANCIA_LUFS)
    # a fala continua lá: ela sobe junto com o ganho do loudnorm, em vez de ser abafada.
    # A tolerância é larga porque `fala_db` é um percentil: a limpeza empurra os quadros
    # silenciosos para baixo e desloca um pouco a distribuição.
    ganho = r.lufs_depois - r.lufs_antes
    assert r.depois.fala_db >= antes.fala_db + ganho - 3.0


@pytest.mark.integration
@requires_ffmpeg
def test_target_loudness_is_respected(fala, tmp_path):
    """O alvo de volume do `.env` (ou das opções) é obedecido."""
    voz, taxa = fala
    entrada = _grava(tmp_path / "voz.wav", 0.4 * voz, taxa)
    for alvo in (-14.0, -20.0):
        r = optimize_audio(
            entrada,
            tmp_path / f"alvo{abs(alvo):.0f}.wav",
            AudioParams.do_env(alvo_lufs=alvo),
            settings=S(cache_dir=tmp_path),
        )
        assert r.lufs_depois == pytest.approx(alvo, abs=TOLERANCIA_LUFS)
        medida = loudness.measure(r.caminho)
        assert medida.i == pytest.approx(alvo, abs=TOLERANCIA_LUFS)


# ------------------------------------------------------------------ padrões do .env


def test_defaults_come_from_the_env(monkeypatch, tmp_path):
    from src.config import load_settings

    env = tmp_path / ".env"
    env.write_text("AUDIO_AGGRESSIVENESS=0.8\nAUDIO_TARGET_LUFS=-14\n", encoding="utf-8")
    monkeypatch.delenv("AUDIO_AGGRESSIVENESS", raising=False)
    monkeypatch.delenv("AUDIO_TARGET_LUFS", raising=False)
    settings = load_settings(env)
    assert settings.audio_aggressiveness == 0.8 and settings.audio_target_lufs == -14

    params = AudioParams.do_env(settings)
    assert params.aggressiveness == 0.8 and params.alvo_lufs == -14
    assert AudioParams.do_env(settings, aggressiveness=0.1).aggressiveness == 0.1  # opções mandam


def test_defaults_without_the_env_are_the_documented_ones():
    from src.config import Settings

    padrao = Settings()
    assert padrao.audio_aggressiveness == 0.5 and padrao.audio_target_lufs == -16.0
    params = AudioParams.do_env(padrao)
    assert (params.aggressiveness, params.alvo_lufs) == (0.5, -16.0)


def test_invalid_env_values_are_rejected(tmp_path, monkeypatch):
    from pydantic import ValidationError

    from src.config import load_settings

    env = tmp_path / ".env"
    env.write_text("AUDIO_AGGRESSIVENESS=3\n", encoding="utf-8")
    monkeypatch.delenv("AUDIO_AGGRESSIVENESS", raising=False)
    with pytest.raises(ValidationError):
        load_settings(env)


def test_pipeline_options_use_the_env_defaults(monkeypatch):
    import src.audio.optimize as optimize_mod
    from src.pipeline import PipelineOptions

    monkeypatch.setattr(
        optimize_mod,
        "get_settings",
        lambda: Settings(audio_aggressiveness=0.9, audio_target_lufs=-12),
    )
    params = PipelineOptions().parametros_audio
    assert params.aggressiveness == 0.9 and params.alvo_lufs == -12
