"""Pipeline completo com vídeos reais de `samples/` (lento; `pytest -m integration`)."""

import subprocess

import pytest

from src.clips import probe_clip
from src.config import OUTPUT_DIR, SAMPLES_DIR
from src.cuts import CutParams, parse_silencedetect
from src.pipeline import run

pytestmark = pytest.mark.integration

SAMPLES = SAMPLES_DIR


def longest_pause(video, noise_db: float) -> float:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(video), "-vn", "-af"]
        + [f"silencedetect=noise={noise_db}dB:d=0.05", "-f", "null", "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    silences = parse_silencedetect(proc.stderr, probe_clip(video).duracao)
    return max((e - s for s, e in silences), default=0.0)


def test_t4_pipeline_removes_long_pauses(tmp_path):
    clips = [SAMPLES / "1.mp4", SAMPLES / "2.mp4"]
    if not all(c.exists() for c in clips):
        pytest.skip("faltam samples/1.mp4 e samples/2.mp4 (veja testes-pendentes.md)")
    params = CutParams()
    out = tmp_path / "final.mp4"
    run(clips, out, params=params)

    meta = probe_clip(out)
    original = sum(probe_clip(c).duracao for c in clips)
    assert meta.fps == pytest.approx(30)
    assert meta.duracao < original
    # Pausa máxima no resultado: < pausa mínima detectável + respiros dos dois lados.
    assert longest_pause(out, params.ruido_db) < params.min_silencio + 2 * params.margem + 0.1
    output_copy = OUTPUT_DIR / "etapa3_samples.mp4"
    output_copy.parent.mkdir(exist_ok=True)
    output_copy.write_bytes(out.read_bytes())  # para a checagem C2 (ouvir)
