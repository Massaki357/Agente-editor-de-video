"""Parte 2, Etapa 5: redução objetiva de tremor em três perfis de câmera."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from src.config import Settings
from src.video.stabilize import VIDSTAB_FILTERS, _ffmpeg_filters, stabilize_video

from .conftest import requires_ffmpeg


def _sample(path: Path, profile: str) -> Path:
    """Três clipes estacionários de 3 s com tremor leve, médio e forte."""
    rng = np.random.default_rng(803)
    blocks = rng.integers(40, 230, size=(24, 40, 3), dtype=np.uint8)
    scene = cv2.resize(blocks, (640, 384), interpolation=cv2.INTER_NEAREST)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (640, 384))
    assert writer.isOpened()
    rng_motion = np.random.default_rng(809)
    for frame in range(90):
        if profile == "leve":
            dx = 2.5 * np.sin(frame * 1.7)
            dy = 2.0 * np.sin(frame * 1.2)
        elif profile == "medio":
            dx = 6.0 * np.sin(frame * 1.4) + 2.0 * np.sin(frame * 0.4)
            dy = 4.0 * np.sin(frame * 1.6)
        else:
            dx = 10.0 * np.sin(frame * 1.5) + rng_motion.normal(0, 2)
            dy = 8.0 * np.sin(frame * 1.9) + rng_motion.normal(0, 2)
        matrix = np.float32([[1, 0, dx], [0, 1, dy]])
        shifted = cv2.warpAffine(scene, matrix, (640, 384), borderMode=cv2.BORDER_REFLECT_101)
        writer.write(shifted)
    writer.release()
    return path


def _motion_variance(path: Path) -> float:
    """Variância do deslocamento x/y entre quadros, medido no miolo da cena."""
    capture = cv2.VideoCapture(str(path))
    previous = None
    deltas = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame[50:-50, 60:-60], cv2.COLOR_BGR2GRAY).astype(np.float32)
        if previous is not None:
            displacement, _ = cv2.phaseCorrelate(previous, gray)
            deltas.append(displacement)
        previous = gray
    capture.release()
    assert len(deltas) == 89
    return float(np.var(deltas, axis=0).sum())


@requires_ffmpeg
@pytest.mark.parametrize("profile", ["leve", "medio", "forte"])
@pytest.mark.parametrize("motor", ["vidstab", "opencv"])
def test_three_shake_profiles_reduce_motion_variance(tmp_path, profile, motor):
    if motor == "vidstab" and not set(VIDSTAB_FILTERS) <= (_ffmpeg_filters() or set()):
        pytest.skip("FFmpeg sem libvidstab")
    original = _sample(tmp_path / f"{profile}.mp4", profile)
    output = stabilize_video(
        original,
        tmp_path / f"{profile}_{motor}.mp4",
        smoothing="medio",
        metodo=motor,
        settings=Settings(cache_dir=tmp_path / "cache"),
    )
    before, after = _motion_variance(original), _motion_variance(output)
    assert before > 1.0
    assert after < before * 0.7, (
        f"{profile}/{motor}: variância de deslocamento {before:.2f} → {after:.2f} px²"
    )
