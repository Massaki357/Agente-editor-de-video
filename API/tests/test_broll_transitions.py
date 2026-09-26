"""Parte 3, Etapa 2: troca de quadros sem alterar a voz ou as emendas."""

import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.broll.transitions import Cutaway, TransitionConfig, apply_cutaways, select_items
from src.images import ItemImagem, ItemZoom, Overlay, PlanoImagens, timeline_signature
from src.pipeline import PipelineOptions, render_project
from src.project import Clip, Project, Timeline
from src.render import render_timeline

from .conftest import make_video, requires_ffmpeg

pytestmark = requires_ffmpeg
FPS = 30
SIZE = (160, 288)


def _blue(path: Path, duration: float = 2.0) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s=160x288:r=30:d={duration}",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return path


def _red_with_audio(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x288:r=30:d=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return path


def _frames(path: Path) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, SIZE[1], SIZE[0], 3)


def _audio(path: Path) -> bytes:
    return subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout


@pytest.mark.parametrize("transition", [
    "hard_cut", "crossfade", "slide", "wipe", "reveal", "zoom", "blur",
])
def test_cutaway_keeps_video_duration_and_original_voice(tmp_path, transition):
    camera = make_video(tmp_path / "camera.mp4", duration=4, size="160x288")
    blue = _blue(tmp_path / "blue.mp4")
    timeline = Timeline(clipes=[Clip(arquivo=str(camera), trechos=[(0, 4)])])
    baseline = render_timeline(timeline, tmp_path / "baseline.mp4", size=SIZE)
    result = render_timeline(
        timeline,
        tmp_path / "with_broll.mp4",
        size=SIZE,
        broll=[Cutaway(1.0, 2.5, blue, 0)],
        broll_transition=transition,
    )
    plain, edited = _frames(baseline), _frames(result)
    assert len(plain) == len(edited) == 120
    assert _audio(baseline) == _audio(result)
    assert np.mean(np.abs(plain[10].astype(int) - edited[10].astype(int))) < 5
    assert edited[50, 144, 80, 2] > 140  # B-roll azul ocupa o quadro inteiro
    assert edited[50, 144, 80, 0] < 50
    assert np.mean(np.abs(plain[100].astype(int) - edited[100].astype(int))) < 5
    assert all(frame.mean() > 10 for frame in edited[27:34])
    assert all(frame.mean() > 10 for frame in edited[72:79])


def test_missing_broll_and_clip_seam_are_safe(tmp_path):
    a = make_video(tmp_path / "a.mp4", duration=2, size="160x288")
    b = make_video(tmp_path / "b.mp4", duration=2, size="160x288")
    blue = _blue(tmp_path / "blue.mp4")
    timeline = Timeline(
        clipes=[
            Clip(arquivo=str(a), trechos=[(0, 2)]),
            Clip(arquivo=str(b), trechos=[(0, 2)]),
        ]
    )
    with pytest.raises(ValueError, match="emenda"):
        render_timeline(
            timeline,
            tmp_path / "invalid.mp4",
            size=SIZE,
            broll=[Cutaway(1.5, 2.5, blue, 0)],
        )
    # Nenhum clipe disponível: a câmera e o áudio seguem normalmente.
    output = render_timeline(timeline, tmp_path / "plain.mp4", size=SIZE, broll=[])
    assert len(_frames(output)) == 120


def test_crossfade_needs_room_for_transition(tmp_path):
    camera = make_video(tmp_path / "camera.mp4", duration=3, size="160x288")
    blue = _blue(tmp_path / "blue.mp4")
    timeline = Timeline(clipes=[Clip(arquivo=str(camera), trechos=[(0, 3)])])
    with pytest.raises(ValueError, match="curto demais"):
        render_timeline(
            timeline,
            tmp_path / "invalid.mp4",
            size=SIZE,
            broll=[Cutaway(1.0, 1.4, blue, 0)],
            broll_transition="crossfade",
        )


def test_two_cutaways_crossfade_preserves_total_frames(tmp_path):
    camera = make_video(tmp_path / "camera.mp4", duration=6, size="160x288")
    blue = _blue(tmp_path / "blue.mp4")
    timeline = Timeline(clipes=[Clip(arquivo=str(camera), trechos=[(0, 6)])])
    result = render_timeline(
        timeline,
        tmp_path / "with_broll.mp4",
        size=SIZE,
        broll=[Cutaway(0.5, 2.0, blue, 0), Cutaway(3.5, 5.0, blue, 0)],
        broll_transition="crossfade",
    )
    frames = _frames(result)
    assert len(frames) == 180
    assert frames[35, 144, 80, 2] > 140
    assert frames[125, 144, 80, 2] > 140
    assert np.mean(np.abs(frames[90].astype(int) - frames[35].astype(int))) > 25


@pytest.mark.parametrize("transition", ["slide", "wipe"])
def test_effect_reveals_next_scene_across_the_frame(tmp_path, transition):
    red = _red_with_audio(tmp_path / "red.mp4")
    blue = _blue(tmp_path / "blue.mp4")
    timeline = Timeline(clipes=[Clip(arquivo=str(red), trechos=[(0, 4)])])
    result = render_timeline(
        timeline,
        tmp_path / "effect.mp4",
        size=SIZE,
        broll=[Cutaway(1, 2.5, blue, 0)],
        broll_transition=transition,
    )
    frames = _frames(result)
    assert len(frames) == 120
    for at in (33, 72):
        left, right = frames[at, 144, 10], frames[at, 144, 150]
        assert abs(int(left[2]) - int(right[2])) > 80


def test_independent_entry_exit_and_unavailable_effect_fallback(tmp_path, monkeypatch):
    import src.broll.transitions as transitions
    from src.render import _clip_meta, plan_segments

    camera = make_video(tmp_path / "camera.mp4", duration=4, size="160x288")
    blue = _blue(tmp_path / "blue.mp4")
    timeline = Timeline(clipes=[Clip(arquivo=str(camera), trechos=[(0, 4)])])
    segments = plan_segments(timeline, [_clip_meta(str(camera), None)], FPS)
    cut = Cutaway(
        1, 2.5, blue, 0,
        TransitionConfig("reveal", duration=0.4, direction="up"),
        TransitionConfig("blur", duration=0.3),
    )
    baseline = render_timeline(timeline, tmp_path / "baseline.mp4", size=SIZE)
    complete = apply_cutaways(
        baseline, tmp_path / "complete.mp4", [cut], segments, FPS,
    )
    assert len(_frames(complete)) == 120
    assert _audio(baseline) == _audio(complete)
    available = transitions.installed_xfade_effects()
    monkeypatch.setattr(transitions, "installed_xfade_effects", lambda: available - {"hblur"})
    warnings = []
    fallback = apply_cutaways(
        baseline, tmp_path / "fallback.mp4", [cut], segments, FPS,
        warnings=warnings,
    )
    assert len(_frames(fallback)) == 120
    assert _audio(baseline) == _audio(fallback)
    assert len(warnings) == 1 and "hblur" in warnings[0] and "corte seco" in warnings[0]
    # Entrada continua animada; a saída sem filtro troca no quadro marcado.
    frames = _frames(fallback)
    assert frames[74, 144, 80, 2] > 140
    assert np.mean(np.abs(frames[75].astype(int) - _frames(baseline)[75].astype(int))) < 5


def test_invalid_parameters_fail_before_render(tmp_path):
    camera = make_video(tmp_path / "camera.mp4", duration=4, size="160x288")
    blue = _blue(tmp_path / "blue.mp4")
    timeline = Timeline(clipes=[Clip(arquivo=str(camera), trechos=[(0, 4)])])
    with pytest.raises(ValueError, match="direção inválida"):
        render_timeline(
            timeline, tmp_path / "invalid.mp4", size=SIZE,
            broll=[Cutaway(1, 2.5, blue, 0,
                           entrada=TransitionConfig("zoom", direction="left"))],
        )


@pytest.mark.parametrize("transition", [
    "hard_cut", "crossfade", "slide", "wipe", "reveal", "zoom", "blur",
])
def test_moving_sources_have_no_black_or_frozen_boundary_frames(tmp_path, transition):
    camera = make_video(tmp_path / "camera.mp4", duration=4, size="160x288")
    broll = tmp_path / "moving.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=s=160x288:r=30:d=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(broll)],
        capture_output=True, check=True,
    )
    timeline = Timeline(clipes=[Clip(arquivo=str(camera), trechos=[(0, 4)])])
    output = render_timeline(
        timeline, tmp_path / "result.mp4", size=SIZE,
        broll=[Cutaway(1, 2.5, broll, 0)], broll_transition=transition,
    )
    frames = _frames(output)
    for start, end in ((27, 34), (72, 79)):
        window = frames[start:end]
        assert all(frame.mean() > 10 for frame in window)
        assert all(np.mean(np.abs(a.astype(int) - b.astype(int))) > 0.1
                   for a, b in zip(window, window[1:], strict=False))


def test_saved_plan_enters_pipeline_and_missing_video_keeps_camera(tmp_path, monkeypatch):
    import src.broll.transitions as transitions
    import src.pipeline as pipeline
    from src.broll.planner import ItemBroll
    from src.broll.source import PreparedBroll
    from src.config import Settings

    camera = make_video(tmp_path / "camera.mp4", duration=4, size="160x288")
    blue = _blue(tmp_path / "blue.mp4")
    project = Project(timeline=Timeline(clipes=[Clip(arquivo=str(camera), trechos=[(0, 4)])]))
    item = ItemBroll(
        id=0,
        clipe=0,
        trecho_inicio_palavra=0,
        trecho_fim_palavra=3,
        texto="colhemos café",
        query="coffee harvest",
        inicio=1,
        duracao_max=1.5,
        motivo="mostra colheita",
        aprovado=True,
    )
    plan = PlanoImagens(assinatura=timeline_signature(project), broll=[item])
    settings = Settings(output_width=160, output_height=288)
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "face_tracks", lambda *a, **k: {})
    monkeypatch.setattr(pipeline, "reframe_cameras", lambda *a, **k: {})
    monkeypatch.setattr(
        transitions,
        "prepare_item",
        lambda *a, **k: PreparedBroll(
            arquivo=blue,
            query=item.query,
            duracao=1.5,
            fonte="pexels",
            id="1",
            pagina="x",
        ),
    )
    options = PipelineOptions(
        cortes=False,
        imagens=False,
        zooms=False,
        legendas=False,
        reenquadrar=True,
        broll=True,
    )
    result = render_project(project, tmp_path / "with_broll.mp4", options, plano=plan)
    assert result.broll == 1 and result.plano is plan
    assert _frames(Path(result.video))[50, 144, 80, 2] > 140

    available = transitions.installed_xfade_effects()
    monkeypatch.setattr(transitions, "installed_xfade_effects", lambda: available - {"hblur"})
    with_fallback = render_project(
        project, tmp_path / "fallback.mp4",
        options.model_copy(update={"broll_transition": "blur"}), plano=plan,
    )
    assert any("hblur" in warning and "corte seco" in warning
               for warning in with_fallback.avisos)

    monkeypatch.setattr(transitions, "prepare_item", lambda *a, **k: None)
    fallback = render_project(project, tmp_path / "camera_only.mp4", options, plano=plan)
    assert fallback.broll == 0
    assert len(_frames(Path(fallback.video))) == 120


def test_three_clips_combine_image_zoom_and_cutaway_without_visual_overlap(tmp_path, monkeypatch):
    """Cada recurso entra no trecho certo; imagem ativa vence B-roll conflitante."""
    from PIL import Image

    import src.broll.transitions as transitions
    import src.pipeline as pipeline
    from src.broll.planner import ItemBroll
    from src.broll.source import PreparedBroll
    from src.config import Settings
    from src.reframe import camera_path

    paths = [make_video(tmp_path / f"{i}.mp4", 4, "160x288") for i in range(3)]
    project = Project(
        timeline=Timeline(clipes=[Clip(arquivo=str(path), trechos=[(0, 4)]) for path in paths])
    )
    project.timeline.recalcular_offsets()
    items = [
        ItemBroll(
            id=i,
            clipe=clip,
            trecho_inicio_palavra=i * 4,
            trecho_fim_palavra=i * 4 + 3,
            texto="frase inteira",
            query=f"video {i}",
            inicio=start,
            duracao_max=1.5,
            motivo="demonstração",
            aprovado=True,
        )
        for i, clip, start in ((0, 0, 1.0), (1, 2, 9.0))
    ]
    plan = PlanoImagens(
        assinatura=timeline_signature(project),
        itens=[
            ItemImagem(
                id=0,
                indice=20,
                clipe=2,
                palavra="imagem",
                query="foto",
                inicio=9.0,
                duracao=1.5,
            )
        ],
        zooms=[
            ItemZoom(id=0, indice=1, clipe=0, palavra="oculto", inicio=1.1, duracao=1.2),
            ItemZoom(id=1, indice=10, clipe=1, palavra="zoom", inicio=5.0, duracao=1.5),
        ],
        broll=items,
    )
    assert select_items(items, [(9.0, 10.5)], 8) == [items[0]]
    green = tmp_path / "image.png"
    Image.new("RGBA", (40, 40), (0, 255, 0, 255)).save(green)
    blue = _blue(tmp_path / "blue.mp4")
    settings = Settings(output_width=160, output_height=288)
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(pipeline, "face_tracks", lambda *a, **k: {})
    monkeypatch.setattr(
        pipeline,
        "reframe_cameras",
        lambda *a, **k: {i: camera_path(None, 160, 288) for i in range(3)},
    )
    monkeypatch.setattr(
        pipeline,
        "build_overlays",
        lambda *a, **k: [Overlay(green, 0, 0, 40, 40, 9.0, 10.5, 0)],
    )
    original_plan_zooms = pipeline.plan_zooms
    seen_zoom_intervals = []

    def record_zooms(intervals, *args, **kwargs):
        seen_zoom_intervals.extend(intervals)
        return original_plan_zooms(intervals, *args, **kwargs)

    monkeypatch.setattr(pipeline, "plan_zooms", record_zooms)
    monkeypatch.setattr(
        transitions,
        "prepare_item",
        lambda item, settings=None: PreparedBroll(
            arquivo=blue, query=item.query, duracao=1.5, fonte="pexels", id="blue", pagina="x"
        ),
    )
    opts = PipelineOptions(
        cortes=False,
        legendas=False,
        imagens=True,
        zooms=True,
        broll=True,
        reenquadrar=True,
    )
    result = render_project(project, tmp_path / "three.mp4", opts, plano=plan)
    assert (result.imagens, result.zooms, result.broll) == (1, 1, 1)
    assert seen_zoom_intervals == [(5.0, 6.5)]
    frames = _frames(Path(result.video))
    assert len(frames) == 360
    assert frames[45, 144, 80, 2] > 140  # B-roll azul no primeiro clipe
    assert np.std(frames[165, :, :, 0]) > 20  # câmera durante o zoom no segundo
    assert frames[285, 10, 10, 1] > 150  # imagem verde no terceiro
    assert np.std(frames[285, :, :, 0]) > 20  # B-roll conflitante foi descartado

    off = render_project(
        project, tmp_path / "three_off.mp4", opts.model_copy(update={"broll": False}), plano=plan
    )
    assert (off.imagens, off.zooms, off.broll) == (1, 2, 0)
    assert len(_frames(Path(off.video))) == 360
