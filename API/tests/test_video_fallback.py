"""Parte 2, Etapa 3: seleção automática e estabilização OpenCV."""

import json
import logging
import subprocess

import cv2
import numpy as np
import pytest

from src.config import Settings
from src.video import cli, stabilize

from .conftest import make_video, requires_ffmpeg
from .test_video_stabilize import _audio_hash, _salto_medio, _video_tremido


def test_selects_opencv_only_when_vidstab_is_missing(monkeypatch):
    monkeypatch.setattr(stabilize, "_ffmpeg_filters", lambda: set(stabilize.VIDSTAB_FILTERS))
    assert stabilize.selecionar_metodo() == "vidstab"
    assert stabilize.selecionar_metodo("opencv") == "opencv"
    monkeypatch.setattr(stabilize, "_ffmpeg_filters", lambda: {"vidstabdetect"})
    assert stabilize.selecionar_metodo() == "opencv"
    with pytest.raises(RuntimeError, match="vidstabtransform"):
        stabilize.selecionar_metodo("vidstab")


@requires_ffmpeg
def test_fallback_reduces_shake_without_black_borders(tmp_path, monkeypatch, caplog):
    entrada = _video_tremido(tmp_path / "tremido.mp4", amplitude=9)
    monkeypatch.setattr(stabilize, "_ffmpeg_filters", lambda: set())
    with caplog.at_level(logging.INFO, logger="src.video.stabilize"):
        saida = stabilize.stabilize_video(
            entrada, tmp_path / "estabilizado.mp4", settings=Settings(cache_dir=tmp_path / "cache")
        )
    assert "opencv (fallback)" in caplog.text
    antes, depois = _salto_medio(entrada), _salto_medio(saida)
    assert depois < antes * 0.8, f"tremor: {antes:.2f} → {depois:.2f} px"
    cap = cv2.VideoCapture(str(saida))
    quadros = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        borda = np.concatenate(
            (
                frame[:5].reshape(-1, 3),
                frame[-5:].reshape(-1, 3),
                frame[:, :5].reshape(-1, 3),
                frame[:, -5:].reshape(-1, 3),
            )
        )
        assert np.mean(np.max(borda, axis=1) < 15) < 0.01
        quadros += 1
    cap.release()
    assert quadros == 60


@requires_ffmpeg
def test_fallback_preserves_audio_and_duration(tmp_path, monkeypatch, capsys):
    entrada = make_video(tmp_path / "entrada.mp4", duration=2)
    monkeypatch.setattr(stabilize, "_ffmpeg_filters", lambda: set())
    monkeypatch.setattr(cli, "setup_logging", lambda **kwargs: None)
    saida = tmp_path / "saida.mp4"
    assert cli.main([str(entrada), str(saida)]) == 0
    assert "Método: opencv (fallback)" in capsys.readouterr().err
    info = json.loads(
        subprocess.run(
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
        ).stdout
    )
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(2, abs=0.1)
    assert _audio_hash(saida) == _audio_hash(entrada)
