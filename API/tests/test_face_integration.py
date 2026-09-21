"""Rastreio de rosto em `samples/3.mp4` (vídeo real; `pytest -m integration`)."""

import time

import numpy as np
import pytest

from src.config import OUTPUT_DIR, SAMPLES_DIR
from src.face import FaceParams, render_debug, track_faces

pytestmark = pytest.mark.integration


def test_t6_real_face_tracking_and_debug_video():
    clip = SAMPLES_DIR / "3.mp4"
    if not clip.exists():
        pytest.skip("falta samples/3.mp4 (veja testes-pendentes.md)")
    track = track_faces(clip, use_cache=False)
    assert track.cobertura > 0.6, "rosto detectado em menos de 60% do vídeo"
    inicio = int(1.5 * track.fps)  # o vídeo começa ~2 s sem rosto
    assert not any(track.detectado[:inicio])
    assert track.at(0.5)[0] == pytest.approx(0.5, abs=0.02)  # começa centralizado
    acel = np.abs(np.diff(np.diff(np.array(track.cx))))
    assert np.percentile(acel, 99) < 0.003  # sem tremor

    started = time.perf_counter()
    assert track_faces(clip) == track  # 2ª execução: cache
    assert time.perf_counter() - started < 1.0

    out = OUTPUT_DIR / "etapa5_debug_samples3.mp4"
    render_debug(clip, out, track, FaceParams().passo)
