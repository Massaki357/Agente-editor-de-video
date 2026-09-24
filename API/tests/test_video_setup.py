"""Parte 2, Etapa 0: checagem e prova de conceito do vidstab."""

import json
import subprocess

import pytest

from src.video import stabilize

from .conftest import make_video, requires_ffmpeg


def test_poc_reports_missing_video_and_filters(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="não encontrado"):
        stabilize.run_poc(tmp_path / "ausente.mp4", tmp_path / "saida.mp4")

    entrada = tmp_path / "entrada.mp4"
    entrada.write_bytes(b"video")
    monkeypatch.setattr(stabilize, "_ffmpeg_filters", lambda: {"vidstabdetect"})
    with pytest.raises(RuntimeError, match="vidstabtransform.*--enable-libvidstab"):
        stabilize.run_poc(entrada, tmp_path / "saida.mp4")


@requires_ffmpeg
def test_poc_runs_both_passes_and_keeps_audio(tmp_path):
    if not {"vidstabdetect", "vidstabtransform"} <= (stabilize._ffmpeg_filters() or set()):
        pytest.skip("FFmpeg sem libvidstab")
    entrada = make_video(tmp_path / "entrada.mp4", duration=1.5)
    saida = stabilize.run_poc(entrada, tmp_path / "saida.mp4")
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
    assert saida.stat().st_size > 0
    assert {stream["codec_type"] for stream in info["streams"]} == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(1.5, abs=0.1)
    assert not list(tmp_path.glob("vidstab_*"))
