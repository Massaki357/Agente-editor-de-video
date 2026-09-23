"""Parte 1, Etapa 0: binário do DeepFilterNet, medidas de ruído e checagens do doctor."""

import platform
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from src.audio import deepfilter, metrics
from src.config import Settings, get_settings
from src.doctor import Status, check_audio

from .conftest import requires_ffmpeg


def S() -> Settings:
    """Settings com o CACHE_DIR isolado do teste."""
    return Settings(cache_dir=get_settings().cache_dir)


def _wav(path: Path, *, ruido_db: float = -40.0, fala: bool = True, dur: float = 2.0) -> Path:
    """WAV 48k mono: 'fala' (tom modulado) sobre um chiado no nível pedido."""
    taxa = 48_000
    t = np.arange(int(taxa * dur)) / taxa
    rng = np.random.default_rng(7)
    sinal = rng.normal(0, 10 ** (ruido_db / 20), len(t))
    if fala:
        envelope = (np.sin(2 * np.pi * 2 * t) > 0).astype(float)  # fala alternando com pausas
        sinal += 0.3 * envelope * np.sin(2 * np.pi * 180 * t)
    dados = np.clip(sinal, -1, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes((dados * 32767).astype(np.int16).tobytes())
    return path


# ------------------------------------------------------------------ medidas


def test_measure_separates_speech_from_the_noise_floor(tmp_path):
    m = metrics.measure(_wav(tmp_path / "a.wav", ruido_db=-50))
    assert m.fala_db > m.ruido_db
    assert m.ruido_db == pytest.approx(-50, abs=6)  # o piso medido é o chiado
    assert 20 < m.snr < 60


def test_noisier_audio_has_a_worse_snr(tmp_path):
    limpo = metrics.measure(_wav(tmp_path / "limpo.wav", ruido_db=-60))
    sujo = metrics.measure(_wav(tmp_path / "sujo.wav", ruido_db=-30))
    assert sujo.snr < limpo.snr - 10


def test_measure_rejects_files_it_cannot_read(tmp_path):
    curto = _wav(tmp_path / "curto.wav", dur=0.001)
    with pytest.raises(ValueError):
        metrics.measure(curto)


@requires_ffmpeg
def test_to_wav_extracts_audio_from_a_video(tmp_path, video_dir):
    saida = metrics.to_wav(video_dir / "1.mp4", tmp_path / "som.wav")
    dados, taxa = metrics.read_wav(saida)
    assert taxa == 48_000 and len(dados) > 10_000


@requires_ffmpeg
def test_to_wav_error_mentions_the_file(tmp_path):
    (tmp_path / "nada.mp4").write_bytes(b"isto nao e video")
    with pytest.raises(RuntimeError, match="nada.mp4"):
        metrics.to_wav(tmp_path / "nada.mp4", tmp_path / "x.wav")


# ------------------------------------------------------------------ binário


def test_platform_asset_matches_this_machine():
    asset = deepfilter.asset_da_plataforma()
    if platform.system() == "Windows":
        assert asset and asset.endswith(".exe")
    assert (asset is None) == (deepfilter.binary_path(S()) is None)


def test_available_is_false_before_downloading(tmp_path, monkeypatch):
    settings = Settings(cache_dir=tmp_path)
    assert deepfilter.available(settings) is False
    with pytest.raises(deepfilter.DeepFilterIndisponivel, match="ainda não baixado"):
        deepfilter.ensure_binary(settings, baixar=False)


def test_truncated_download_is_not_accepted(tmp_path):
    settings = Settings(cache_dir=tmp_path)
    path = deepfilter.binary_path(settings)
    if path is None:
        pytest.skip("plataforma sem binário do DeepFilterNet")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * 1000)  # download interrompido
    assert deepfilter.available(settings) is False


def test_unsupported_platform_explains_the_fallback(monkeypatch):
    monkeypatch.setattr(deepfilter.platform, "system", lambda: "Haiku")
    monkeypatch.setattr(deepfilter.platform, "machine", lambda: "sparc")
    assert deepfilter.asset_da_plataforma() is None
    with pytest.raises(deepfilter.DeepFilterIndisponivel, match="RNNoise|noisereduce"):
        deepfilter.ensure_binary(S())


def test_enhance_requires_an_existing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        deepfilter.enhance(tmp_path / "nao_existe.wav", tmp_path / "saida.wav", S())


# ------------------------------------------------------------------ doctor


def test_doctor_reports_the_binary_when_it_is_in_the_cache(tmp_path, monkeypatch):
    settings = Settings(cache_dir=tmp_path)
    path = deepfilter.binary_path(settings)
    if path is None:
        pytest.skip("plataforma sem binário do DeepFilterNet")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * (deepfilter.TAMANHO_MIN + 1))
    (df,) = [c for c in check_audio(settings) if c.name == "DeepFilterNet"]
    assert df.status is Status.OK and deepfilter.VERSAO in df.detail


def test_doctor_warns_instead_of_failing_when_the_binary_is_missing(tmp_path):
    (df,) = [c for c in check_audio(Settings(cache_dir=tmp_path)) if c.name == "DeepFilterNet"]
    assert df.status is Status.WARN
    assert "baixado no 1º uso" in df.detail or "sem binário" in df.detail


def test_doctor_warns_on_a_platform_without_a_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(deepfilter.platform, "system", lambda: "Haiku")
    monkeypatch.setattr(deepfilter.platform, "machine", lambda: "sparc")
    (df,) = [c for c in check_audio(Settings(cache_dir=tmp_path)) if c.name == "DeepFilterNet"]
    assert df.status is Status.WARN and "RNNoise" in df.detail


@requires_ffmpeg
def test_doctor_lists_the_audio_filters(tmp_path):
    (filtros,) = [
        c for c in check_audio(Settings(cache_dir=tmp_path)) if c.name == "Filtros de áudio"
    ]
    assert filtros.status in (Status.OK, Status.WARN)
    assert "loudnorm" in filtros.detail


# ------------------------------------------------------------------ limpeza de verdade


@pytest.mark.integration
@requires_ffmpeg
def test_deepfilter_cleans_real_speech_with_added_noise(tmp_path):
    """Critério de aceite da etapa: fala real com ruído sai audivelmente mais limpa.

    Precisa de um vídeo/áudio com voz em `samples/` — um tom sintético não serve, o
    DeepFilterNet foi treinado em fala e trataria o tom como ruído.
    """
    from src.config import SAMPLES_DIR

    fontes = sorted(
        p for p in SAMPLES_DIR.iterdir() if p.suffix.lower() in {".mp4", ".wav", ".mp3"}
    )
    if not fontes:
        pytest.skip("coloque um vídeo com voz em samples/ (veja testes-pendentes.md)")

    limpo = metrics.to_wav(fontes[0], tmp_path / "voz.wav")
    dados, taxa = metrics.read_wav(limpo)
    dados = dados[: taxa * 15]  # 15 s bastam e o teste fica rápido
    rng = np.random.default_rng(3)
    ruidoso = np.clip(dados + rng.normal(0, 10 ** (-35 / 20), len(dados)), -1, 1)
    entrada = tmp_path / "voz_ruidosa.wav"
    with wave.open(str(entrada), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes((ruidoso * 32767).astype(np.int16).tobytes())

    antes = metrics.measure(entrada)
    saida = deepfilter.enhance(entrada, tmp_path / "voz_limpa.wav")
    depois = metrics.measure(saida)

    assert depois.ruido_db < antes.ruido_db - 15  # o chiado de fundo some
    assert depois.snr > antes.snr + 15
    assert depois.fala_db == pytest.approx(antes.fala_db, abs=4)  # a voz continua lá
    # a duração não muda (--compensate-delay), senão o áudio desalinha do vídeo
    assert len(metrics.read_wav(saida)[0]) == pytest.approx(len(ruidoso), rel=0.01)


@pytest.mark.integration
def test_binary_downloads_once_and_is_reused(tmp_path):
    settings = Settings(cache_dir=tmp_path)
    if deepfilter.asset_da_plataforma() is None:
        pytest.skip("plataforma sem binário do DeepFilterNet")
    path = deepfilter.ensure_binary(settings)
    assert path.is_file() and path.stat().st_size > deepfilter.TAMANHO_MIN
    mtime = path.stat().st_mtime
    assert deepfilter.ensure_binary(settings) == path  # 2ª chamada não baixa de novo
    assert path.stat().st_mtime == mtime
    ajuda = subprocess.run([str(path), "--help"], capture_output=True, text=True, timeout=60)
    assert ajuda.returncode == 0 and "atten-lim-db" in ajuda.stdout
