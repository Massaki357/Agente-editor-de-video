"""Etapa 6: janela 9:16 que segue o rosto e render reenquadrado em 1080x1920."""

import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.clips import probe_clip, project_from_files
from src.face import FaceTrack
from src.reframe import ReframeParams, camera_path, crop_size, limit_speed
from src.render import render_timeline

FPS = 30
SAIDA = (1080, 1920)


def track(cx, cy=None, fps=30.0, largura=1280, altura=720) -> FaceTrack:
    cx = list(cx)
    cy = list(cy) if cy is not None else [0.4] * len(cx)
    return FaceTrack(
        fps=fps,
        largura=largura,
        altura=altura,
        cx=cx,
        cy=cy,
        w=[0.1] * len(cx),
        h=[0.18] * len(cx),
        detectado=[True] * len(cx),
    )


# ------------------------------------------------------------------ geometria


@pytest.mark.parametrize(
    ("largura", "altura", "esperado"),
    [
        (1920, 1080, (608, 1080)),
        (1280, 720, (404, 720)),
        (1080, 1920, (1080, 1920)),  # já é 9:16
        (1080, 1080, (608, 1080)),
        (1080, 2400, (1080, 1920)),  # mais alto que 9:16: corta na vertical
    ],
)
def test_crop_size_is_9_16_and_fits(largura, altura, esperado):
    cw, ch = crop_size(largura, altura)
    assert (cw, ch) == esperado
    assert cw <= largura and ch <= altura and cw % 2 == 0 and ch % 2 == 0
    assert cw / ch == pytest.approx(9 / 16, abs=0.01)


def test_without_face_the_window_is_centered():
    cam = camera_path(None, 1280, 720)
    assert cam.window(0.0) == ((1280 - 404) // 2, 0, 404, 720)
    assert cam.window(99.0) == cam.window(0.0)


def test_window_follows_face_and_never_leaves_the_frame():
    cam = camera_path(track([0.02] * 30 + [0.5] * 90 + [0.98] * 60), 1280, 720)
    assert cam.window(0.1)[0] == 0  # rosto na borda esquerda: janela encostada
    x, _, cw, _ = cam.window(2.5)
    assert x + cw / 2 == pytest.approx(640, abs=2)  # rosto no centro
    x, _, cw, _ = cam.window(5.9)
    assert x == 1280 - cw  # borda direita
    for t in np.linspace(0, 6, 200):
        x, y, cw, ch = cam.window(t)
        assert 0 <= x and x + cw <= 1280 and y == 0 and ch == 720


def test_camera_speed_is_limited_without_lag():
    cx = [0.2] * 60 + [0.8] * 60  # o rosto "salta" (ex.: rastreio perdeu e voltou)
    params = ReframeParams(vel_max=0.5)
    cam = camera_path(track(cx), 1280, 720, params=params)
    passo = np.abs(np.diff(cam.x))
    assert passo.max() <= 0.5 * 1280 / FPS + 1e-6
    meio = int(np.argmin(np.abs(cam.x - (cam.x[0] + cam.x[-1]) / 2)))
    assert meio == pytest.approx(60, abs=2)  # a transição fica centrada no salto: sem atraso


def test_limit_speed_keeps_slow_motion_untouched():
    x = np.linspace(0, 10, 100)
    assert np.allclose(limit_speed(x, 1.0), x)


def test_tall_source_slides_vertically_with_headroom():
    cam = camera_path(track([0.5] * 10, cy=[0.5] * 10, largura=1080, altura=2400), 1080, 2400)
    x, y, cw, ch = cam.window(0.1)
    assert (x, cw, ch) == (0, 1080, 1920)
    assert y == pytest.approx(0.5 * 2400 - 0.4 * 1920, abs=2)  # rosto a 40% da janela
    topo = camera_path(track([0.5] * 10, cy=[0.1] * 10, largura=1080, altura=2400), 1080, 2400)
    assert topo.window(0.1)[1] == 0  # não sai do quadro


def test_face_box_in_output_coordinates():
    cam = camera_path(track([0.5] * 10), 1280, 720)
    cx, cy, w, h = cam.to_output((0.5, 0.4, 0.1, 0.18), 0.1, SAIDA)
    assert cx == pytest.approx(540, abs=4)
    assert cy == pytest.approx(0.4 * 1920, abs=4)
    assert w == pytest.approx(0.1 * 1280 * 1080 / 404, rel=0.01)


# ------------------------------------------------------------------ render reenquadrado


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def terços(tmp_path_factory) -> Path:
    """1280x720, 2 s: terço esquerdo vermelho, meio verde, direito azul; com áudio."""
    out = tmp_path_factory.mktemp("reframe") / "cores.mp4"
    cor = "color=c={}:s=428x720:r=30:d=2"
    _ffmpeg(
        "-f", "lavfi", "-i", cor.format("red"),
        "-f", "lavfi", "-i", cor.format("lime"),
        "-f", "lavfi", "-i", cor.format("blue"),
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-filter_complex", "[0][1][2]hstack=3,crop=1280:720:0:0[v]",
        "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(out),
    )  # fmt: skip
    return out


def _frame(video: Path, t: float) -> np.ndarray:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", str(t)]
        + ["-i", str(video), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(proc.stdout, np.uint8).reshape(1920, 1080, 3)


@pytest.mark.parametrize(("cx", "canal"), [(1 / 6, 0), (0.5, 1), (5 / 6, 2)])
def test_reframed_render_shows_the_region_around_the_face(terços, tmp_path, cx, canal):
    project = project_from_files([terços])
    cam = camera_path(track([cx] * 60), 1280, 720)
    out = render_timeline(project.timeline, tmp_path / "out.mp4", size=SAIDA, cameras={0: cam})
    meta = probe_clip(out)
    assert (meta.largura, meta.altura, meta.fps) == (1080, 1920, 30)
    media = _frame(out, 1.0).reshape(-1, 3).mean(axis=0)
    assert np.argmax(media) == canal and media[canal] > 150


def _flashes_e_bipes(path: Path) -> Path:
    """4 s: tela preta com flash branco (1 frame) + bipe de 50 ms em 0,5 / 2,0 / 3,5 s."""
    instantes = (0.5, 2.0, 3.5)
    flash = "".join(
        f",drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(t,{t},{t + 0.03})'"
        for t in instantes
    )
    bipe = "+".join(f"between(t,{t},{t + 0.05})" for t in instantes)
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c=black:s=1280x720:r=30:d=4{flash}",
        "-f", "lavfi", "-i", f"aevalsrc='0.8*sin(2*PI*1000*t)*({bipe})':s=48000:d=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    )  # fmt: skip
    return path


def _onsets_video(video: Path) -> list[float]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(video)]
        + ["-vf", "scale=54:96", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True,
        capture_output=True,
    )
    brilho = np.frombuffer(proc.stdout, np.uint8).reshape(-1, 96 * 54).mean(axis=1)
    claros = np.flatnonzero(brilho > 128)
    return [i / FPS for i in claros if i - 1 not in claros]


def _onsets_audio(video: Path) -> list[float]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(video)]
        + ["-ac", "1", "-ar", "48000", "-f", "s16le", "-"],
        check=True,
        capture_output=True,
    )
    s = np.abs(np.frombuffer(proc.stdout, np.int16).astype(float))
    altos = np.flatnonzero(s > 0.3 * 32767)
    return [altos[0] / 48000] + [
        altos[k] / 48000 for k in range(1, len(altos)) if altos[k] - altos[k - 1] > 4800
    ]


@pytest.mark.parametrize("reenquadrar", [True, False], ids=["9x16", "quadro-original"])
def test_audio_stays_in_sync_at_start_middle_and_end(tmp_path, reenquadrar):
    """Critério da Etapa 6: A/V sincronizado no começo, meio e fim, com cortes.

    Flashes e bipes caem em frames inteiros, então a diferença deve ser ~0 (o seek de
    entrada do AAC deixava o áudio 13–40 ms adiantado; agora o áudio usa `atrim`).
    """
    a = _flashes_e_bipes(tmp_path / "a.mp4")
    b = _flashes_e_bipes(tmp_path / "b.mp4")
    project = project_from_files([a, b])
    project.timeline.substituir_trechos(0, [(0.3, 1.2), (1.8, 2.6)])
    project.timeline.substituir_trechos(1, [(0.0, 1.0), (3.2, 4.0)])
    cams = {0: camera_path(None, 1280, 720), 1: camera_path(None, 1280, 720)}
    if reenquadrar:
        out = render_timeline(project.timeline, tmp_path / "out.mp4", size=SAIDA, cameras=cams)
    else:
        out = render_timeline(project.timeline, tmp_path / "out.mp4")

    video, audio = _onsets_video(out), _onsets_audio(out)
    # t_out dos flashes: clipe a = [0.3,1.2]+[1.8,2.6] (1,7 s); clipe b = [0,1]+[3.2,4]
    # 0.5→0.2, 2.0→0.9+0.2=1.1, b0.5→1.7+0.5=2.2, b3.5→1.7+1.0+0.3=3.0
    assert video == pytest.approx([0.2, 1.1, 2.2, 3.0], abs=1.5 / FPS)
    assert len(audio) == len(video)
    for tv, ta in zip(video, audio, strict=True):
        assert abs(tv - ta) <= 0.005, (tv, ta)  # começo, meio e fim sincronizados


def test_reframed_render_keeps_exact_duration_and_av_length(terços, tmp_path):
    project = project_from_files([terços, terços])
    project.timeline.substituir_trechos(0, [(0.0, 0.5), (1.0, 1.7)])
    cams = {0: camera_path(None, 1280, 720), 1: camera_path(None, 1280, 720)}
    out = render_timeline(project.timeline, tmp_path / "out.mp4", size=SAIDA, cameras=cams)
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration,nb_frames"]
        + ["-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    info = {s.split(",")[0]: s.split(",")[1:] for s in streams}
    esperado = 0.5 + 0.7 + 2.0
    assert float(info["video"][0]) == pytest.approx(esperado, abs=1 / FPS)
    assert int(info["video"][1]) == round(esperado * FPS)
    assert float(info["audio"][0]) == pytest.approx(float(info["video"][0]), abs=1 / FPS)


def _rgb_medio(video: Path, matriz: str) -> np.ndarray:
    """Cor média de um frame como um player vê (YUV→RGB com a matriz dada)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(video)]
        + ["-frames:v", "1", "-vf", f"scale=64:64:in_color_matrix={matriz}"]
        + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(proc.stdout, np.uint8).reshape(-1, 3).mean(axis=0)


@pytest.mark.parametrize("marcado", [True, False], ids=["bt709-marcado", "sem-marcacao"])
def test_reframe_preserves_colors_of_hd_sources(tmp_path, marcado):
    """Vermelho-alaranjado chapado 0xD03020 em 1920x1080 (HD: players usam BT.709)."""
    fonte = tmp_path / "cor.mp4"
    tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=0xD03020:s=1920x1080:r=30:d=1",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        *(tags if marcado else []),
        "-c:v", "libx264", "-preset", "ultrafast", str(fonte),
    )  # fmt: skip
    project = project_from_files([fonte])
    cams = {0: camera_path(None, 1920, 1080)}
    out = render_timeline(project.timeline, tmp_path / "out.mp4", size=SAIDA, cameras=cams)

    esperado = _rgb_medio(fonte, "bt709")
    assert np.abs(esperado - [0xD0, 0x30, 0x20]).max() <= 8  # sanidade da fonte (H.264)
    assert np.abs(_rgb_medio(out, "bt709") - esperado).max() <= 3
    cor = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries"]
        + ["stream=color_space,color_primaries,color_transfer", "-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert cor == "bt709,bt709,bt709"
