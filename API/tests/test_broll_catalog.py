"""Parte 6, Etapa 0: catálogo, parâmetros e prévias de entrada/saída."""

from __future__ import annotations

import hashlib
import json
import subprocess

import cv2
import numpy as np
import pytest

from src.broll.catalog import (
    PRESETS,
    choose,
    generate_catalog,
    installed_xfade_effects,
    render_preview,
    validate_placement,
)

from .conftest import requires_ffmpeg


def test_presets_are_described_and_defaults_fit_short_cutaways():
    assert len(PRESETS) == 7
    assert len({preset.id for preset in PRESETS}) == len(PRESETS)
    assert {preset.categoria for preset in PRESETS} == {
        "corte", "fade", "movimento", "revelação", "estilizado",
    }
    for preset in PRESETS:
        assert preset.nome and preset.descricao and preset.custo
        choice = choose(preset.id)
        assert preset.duracao_min <= choice.duracao <= preset.duracao_max + 1 / 30
        validate_placement(
            choice, fps=30, start=1, end=2.5, clip_start=0, clip_end=4,
        )


@pytest.mark.parametrize(("options", "error"), [
    ({"preset_id": "unknown"}, "desconhecido"),
    ({"preset_id": "slide", "fps": 0}, "fps"),
    ({"preset_id": "zoom", "fps": 3, "duration": 0.5}, "30 fps"),
    ({"preset_id": "slide", "duration": -0.1}, "duração"),
    ({"preset_id": "slide", "duration": 0.9}, "duração"),
    ({"preset_id": "slide", "duration": float("nan")}, "duração"),
    ({"preset_id": "slide", "duration": "rápido"}, "duração"),
    ({"preset_id": "slide", "direction": "diagonal"}, "direção"),
    ({"preset_id": "blur", "direction": "left"}, "direção"),
    ({"preset_id": "zoom", "intensity": 1.5}, "intensidade"),
    ({"preset_id": "crossfade", "intensity": float("inf")}, "intensidade"),
    ({"preset_id": "hard_cut", "duration": 0.25}, "duração"),
])
def test_invalid_choice_is_rejected_before_render(options, error, monkeypatch):
    monkeypatch.setattr(
        "src.broll.catalog.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("FFmpeg não deve rodar"),
    )
    with pytest.raises(ValueError, match=error):
        choose(**options)


def test_direction_intensity_and_frame_quantization():
    choice = choose("wipe", direction="up", duration=0.25)
    assert choice.frames == 8
    assert choice.duracao == pytest.approx(8 / 30)
    assert (choice.entrada, choice.saida) == ("wipeup", "wipedown")
    assert choose("crossfade", intensity=0.5).entrada == "fadeslow"
    assert choose("crossfade", intensity=1.5).saida == "fadefast"
    reveal = choose("reveal", duration=0.15)
    assert reveal.frames == 5
    assert 0.15 <= reveal.duracao <= 0.5


@pytest.mark.parametrize(("window", "error"), [
    ({"start": 0.1, "end": 1.7, "clip_start": 0, "clip_end": 3}, "curto"),
    ({"start": 0.8, "end": 1.2, "clip_start": 0, "clip_end": 3}, "curto"),
    ({"start": 1, "end": 2.8, "clip_start": 0, "clip_end": 3}, "curto"),
    ({"start": 1, "end": 3.1, "clip_start": 0, "clip_end": 3}, "fora"),
    ({"start": 1, "end": 2.5, "clip_start": 0, "clip_end": 4,
      "previous_end": 0.8}, "intervalo"),
    ({"start": 1, "end": 2.5, "clip_start": 0, "clip_end": 4,
      "next_start": 2.6}, "intervalo"),
])
def test_short_clips_seams_and_neighboring_cutaways_are_rejected(window, error):
    with pytest.raises(ValueError, match=error):
        validate_placement(choose("zoom", duration=0.5), fps=30, **window)


def _video_frames(path):
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return np.stack(frames)


@requires_ffmpeg
def test_catalog_renders_entry_and_exit_previews_with_installed_effects(tmp_path):
    installed = installed_xfade_effects()
    assert "fade" in installed and "zoomin" in installed and "hblur" in installed
    manifest = generate_catalog(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(data["presets"]) == len(PRESETS)
    assert (tmp_path / "index.html").is_file()
    hard_cut = _video_frames(tmp_path / "hard_cut.mp4")
    audio_hashes = set()
    for entry in data["presets"]:
        assert entry["nome"] and entry["descricao"] and entry["custo"]
        assert entry["render_segundos"] > 0
        assert entry["xfade_entrada"] is None or entry["xfade_entrada"] in installed
        assert entry["xfade_saida"] is None or entry["xfade_saida"] in installed
        frames = _video_frames(tmp_path / entry["arquivo"])
        audio = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i",
             str(tmp_path / entry["arquivo"]), "-map", "0:a:0", "-f", "s16le",
             "-ac", "1", "-ar", "48000", "-"],
            capture_output=True, check=True,
        ).stdout
        assert audio
        audio_hashes.add(hashlib.sha256(audio).hexdigest())
        assert len(frames) == 135
        assert frames.shape[1:3] == (320, 180)
        assert np.min(np.mean(frames, axis=(1, 2, 3))) > 10
        assert np.mean(np.abs(frames[20].astype(int) - frames[70].astype(int))) > 8
        assert np.mean(np.abs(frames[70].astype(int) - frames[125].astype(int))) > 8
        if entry["id"] != "hard_cut":
            for frame in (49, 94):  # entrada e saída diferem do corte imediato
                assert np.mean(np.abs(frames[frame].astype(int) - hard_cut[frame].astype(int))) > 8
    assert len(audio_hashes) == 1


def test_preview_rejects_invalid_dimensions_before_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.broll.catalog.installed_xfade_effects",
        lambda: pytest.fail("FFmpeg não deve rodar"),
    )
    with pytest.raises(ValueError, match="dimensões"):
        render_preview(choose("blur"), tmp_path / "invalid.mp4", width=181)
