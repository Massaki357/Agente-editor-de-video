"""Parte 2, Etapa 1: parâmetros, cache e preservação do áudio."""

import hashlib
import json
import subprocess

import cv2
import numpy as np
import pytest

from src.config import Settings
from src.video import opencv_fallback, stabilize

from .conftest import make_video, requires_ffmpeg


def test_cache_uses_file_hash_and_parameters(tmp_path, monkeypatch):
    entrada = tmp_path / "entrada.mp4"
    entrada.write_bytes(b"clipe-1")
    settings = Settings(cache_dir=tmp_path / "cache")
    chamadas = []

    def render(origem, destino, smoothing_frames, crop_percent, on_progress):
        chamadas.append(("vidstab", smoothing_frames, crop_percent))
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(f"resultado-{len(chamadas)}".encode())

    def render_fallback(origem, destino, smoothing_frames, crop_percent, on_progress):
        chamadas.append(("opencv", smoothing_frames, crop_percent))
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(f"resultado-{len(chamadas)}".encode())

    monkeypatch.setattr(stabilize, "_ffmpeg_filters", lambda: set(stabilize.VIDSTAB_FILTERS))
    monkeypatch.setattr(stabilize, "_render_vidstab", render)
    monkeypatch.setattr(opencv_fallback, "render_opencv", render_fallback)

    saida = tmp_path / "saida.mp4"
    stabilize.stabilize_video(entrada, saida, settings=settings)
    assert saida.read_bytes() == b"resultado-1"
    assert chamadas == [("vidstab", 12, None)]

    # Mesmo clipe, parâmetros e motor: aproveita o cache.
    stabilize.stabilize_video(entrada, saida, settings=settings)
    assert chamadas == [("vidstab", 12, None)]

    # Sem vidstab, usa OpenCV e um cache separado; a segunda chamada o reutiliza.
    monkeypatch.setattr(stabilize, "_ffmpeg_filters", lambda: set())
    stabilize.stabilize_video(entrada, saida, settings=settings)
    assert saida.read_bytes() == b"resultado-2"
    stabilize.stabilize_video(entrada, saida, settings=settings)
    assert len(chamadas) == 2

    monkeypatch.setattr(stabilize, "_ffmpeg_filters", lambda: set(stabilize.VIDSTAB_FILTERS))
    stabilize.stabilize_video(entrada, saida, settings=settings)
    assert saida.read_bytes() == b"resultado-1"
    stabilize.stabilize_video(entrada, saida, smoothing="forte", settings=settings)
    stabilize.stabilize_video(entrada, saida, smoothing="forte", crop_percent=5, settings=settings)
    entrada.write_bytes(b"clipe-2-diferente")
    stabilize.stabilize_video(entrada, saida, settings=settings)
    assert chamadas == [
        ("vidstab", 12, None),
        ("opencv", 12, None),
        ("vidstab", 24, None),
        ("vidstab", 24, 5),
        ("vidstab", 12, None),
    ]
    assert len(list((settings.cache_dir / "video_estabilizado").glob("*.mp4"))) == 5


def test_env_defaults_affect_renderer_and_cache(tmp_path, monkeypatch):
    entrada = tmp_path / "entrada.mp4"
    entrada.write_bytes(b"clipe-1")
    settings = Settings(
        cache_dir=tmp_path / "cache", stabilize_smoothing="forte", stabilize_crop_percent=4.5
    )
    chamados = []

    def render(origem, destino, smoothing_frames, crop_percent, on_progress):
        chamados.append((smoothing_frames, crop_percent))
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"resultado")

    monkeypatch.setattr(stabilize, "_ffmpeg_filters", lambda: set(stabilize.VIDSTAB_FILTERS))
    monkeypatch.setattr(stabilize, "_render_vidstab", render)
    path = stabilize.cached_video(entrada, settings=settings)
    assert path == stabilize.cached_video_file(entrada, settings=settings)
    assert chamados == [(24, 4.5)]
    assert stabilize.cached_video(entrada, settings=settings) == path
    assert chamados == [(24, 4.5)]
    other = stabilize.stabilize_video(
        entrada, tmp_path / "explicit.mp4", smoothing="leve", crop_percent=0, settings=settings
    )
    assert other.is_file()
    assert chamados == [(24, 4.5), (5, 0)]
    assert stabilize.cache_key(entrada, "forte", 4.5) in path.name


def test_invalid_parameters_do_not_touch_output(tmp_path):
    entrada = tmp_path / "entrada.mp4"
    entrada.write_bytes(b"clipe")
    saida = tmp_path / "saida.mp4"
    saida.write_bytes(b"original")
    for kwargs in ({"smoothing": "enorme"}, {"crop_percent": -1}, {"crop_percent": 31}):
        with pytest.raises(ValueError):
            stabilize.stabilize_video(entrada, saida, **kwargs)
        assert saida.read_bytes() == b"original"
    with pytest.raises(ValueError, match="diferente"):
        stabilize.stabilize_video(entrada, entrada)


def _audio_hash(path):
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-c",
            "copy",
            "-f",
            "hash",
            "-hash",
            "SHA256",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


@requires_ffmpeg
@pytest.mark.parametrize(
    "smoothing,crop_percent", [("leve", None), ("medio", None), ("forte", None), ("medio", 5.0)]
)
def test_smoothing_and_manual_crop_keep_audio_and_duration(tmp_path, smoothing, crop_percent):
    if not set(stabilize.VIDSTAB_FILTERS) <= (stabilize._ffmpeg_filters() or set()):
        pytest.skip("FFmpeg sem libvidstab")
    entrada = make_video(tmp_path / "entrada.mp4", duration=1.5)
    saida = stabilize.stabilize_video(
        entrada,
        tmp_path / f"saida_{smoothing}.mp4",
        smoothing=smoothing,
        crop_percent=crop_percent,
        settings=Settings(cache_dir=tmp_path / "cache"),
    )
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(saida),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    info = json.loads(proc.stdout)
    assert {stream["codec_type"] for stream in info["streams"]} == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(1.5, abs=0.1)
    assert _audio_hash(saida) == _audio_hash(entrada)


def test_cache_key_changes_with_version(tmp_path, monkeypatch):
    entrada = tmp_path / "clipe.mp4"
    entrada.write_bytes(hashlib.sha256(b"clipe").digest())
    antiga = stabilize.cache_key(entrada, "medio", None)
    monkeypatch.setattr(stabilize, "CADEIA_VERSION", stabilize.CADEIA_VERSION + 1)
    assert stabilize.cache_key(entrada, "medio", None) != antiga


def _video_tremido(path, amplitude):
    """Cena estacionária colorida com tremor controlado, sem pixels pretos."""
    rng = np.random.default_rng(42)
    blocos = rng.choice(np.array([35, 220], dtype=np.uint8), size=(15, 27, 3))
    quadro = cv2.resize(blocos, (320, 180), interpolation=cv2.INTER_NEAREST)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (320, 180))
    assert writer.isOpened()
    for frame in range(60):
        dx = round(amplitude * np.sin(frame * 1.8))
        dy = round(amplitude * np.sin(frame * 2.2))
        writer.write(np.roll(quadro, (dy, dx), axis=(0, 1)))
    writer.release()
    return path


def _salto_medio(path):
    cap = cv2.VideoCapture(str(path))
    anterior = None
    saltos = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        cinza = cv2.cvtColor(frame[30:-30, 50:-50], cv2.COLOR_BGR2GRAY).astype(np.float32)
        if anterior is not None:
            (dx, dy), _ = cv2.phaseCorrelate(anterior, cinza)
            saltos.append(np.hypot(dx, dy))
        anterior = cinza
    cap.release()
    return float(np.mean(saltos))


@requires_ffmpeg
@pytest.mark.parametrize("amplitude", [2, 5, 9])
@pytest.mark.parametrize("smoothing", ["leve", "medio", "forte"])
def test_synthetic_shake_has_no_black_borders(tmp_path, amplitude, smoothing):
    if not set(stabilize.VIDSTAB_FILTERS) <= (stabilize._ffmpeg_filters() or set()):
        pytest.skip("FFmpeg sem libvidstab")
    entrada = _video_tremido(tmp_path / "tremido.mp4", amplitude)
    saida = stabilize.stabilize_video(
        entrada,
        tmp_path / "estabilizado.mp4",
        smoothing=smoothing,
        settings=Settings(cache_dir=tmp_path / "cache"),
    )
    cap = cv2.VideoCapture(str(saida))
    assert cap.isOpened()
    quadros = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        # A cena não tem preto: faixa preta no contorno denuncia crop insuficiente.
        borda = np.concatenate(
            (
                frame[:6].reshape(-1, 3),
                frame[-6:].reshape(-1, 3),
                frame[:, :6].reshape(-1, 3),
                frame[:, -6:].reshape(-1, 3),
            )
        )
        assert np.mean(np.max(borda, axis=1) < 15) < 0.01
        quadros += 1
    cap.release()
    assert quadros == 60
    antes, depois = _salto_medio(entrada), _salto_medio(saida)
    assert depois < antes * 0.9, f"tremor {amplitude}/{smoothing}: {antes:.2f} → {depois:.2f} px"
