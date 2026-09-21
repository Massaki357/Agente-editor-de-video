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


def test_t7_real_clip_reframed_to_9_16():
    from src.clips import probe_clip
    from src.pipeline import PipelineOptions, build_project, render_project

    clip = SAMPLES_DIR / "3.mp4"
    if not clip.exists():
        pytest.skip("falta samples/3.mp4 (veja testes-pendentes.md)")
    out = OUTPUT_DIR / "etapa6_samples3_9x16.mp4"
    render_project(build_project([clip]), out, PipelineOptions(cortes_fala=False))
    meta = probe_clip(out)
    assert (meta.largura, meta.altura, meta.fps) == (1080, 1920, 30)
