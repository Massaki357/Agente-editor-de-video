"""Etapa 11: fontes difíceis — fps variável, HDR e arquivos bem maiores que a saída."""

import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.clips import _is_vfr, probe_clip, project_from_files
from src.face import FaceTrack
from src.reframe import camera_path
from src.render import _decode_color_filter, _is_hdr, render_timeline

from .conftest import requires_ffmpeg

pytestmark = requires_ffmpeg

SAIDA = (1080, 1920)


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args],
        check=True,
        capture_output=True,
    )


def _track(largura: int, altura: int, n: int = 60) -> FaceTrack:
    return FaceTrack(
        fps=30, largura=largura, altura=altura, cx=[0.5] * n, cy=[0.5] * n,
        w=[0.1] * n, h=[0.18] * n, detectado=[True] * n, caixa=[[0.5, 0.5, 0.1, 0.18]] * n,
    )  # fmt: skip


# ------------------------------------------------------------------ fps variável


@pytest.mark.parametrize(
    ("avg", "base", "esperado"),
    [
        ("30/1", "30/1", False),
        ("30000/1001", "30000/1001", False),
        ("2997/100", "30/1", False),  # 0,1% de diferença: arredondamento, não VFR
        ("24/1", "30/1", True),  # média bem abaixo da taxa base
        ("0/0", "30/1", False),  # desconhecido: não arrisca
    ],
)
def test_variable_frame_rate_detection(avg, base, esperado):
    assert _is_vfr({"avg_frame_rate": avg, "r_frame_rate": base}) is esperado


def test_constant_rate_file_is_not_vfr(tmp_path):
    saida = tmp_path / "cfr.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(saida),
    )  # fmt: skip
    meta = probe_clip(saida)
    assert meta.vfr is False and meta.hdr is False and meta.fps == pytest.approx(30, abs=0.01)


# ------------------------------------------------------------------ HDR


@pytest.fixture(scope="module")
def hdr(tmp_path_factory) -> Path:
    """Clipe marcado como PQ/BT.2020 (HDR), com uma faixa clara no centro."""
    saida = tmp_path_factory.mktemp("hdr") / "hdr.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=30:d=1",
        "-vf", "drawbox=x=300:y=140:w=80:h=80:color=white:t=fill,format=yuv420p10le,"
        "setparams=color_trc=smpte2084:colorspace=bt2020nc:color_primaries=bt2020",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le", str(saida),
    )  # fmt: skip
    return saida


def test_hdr_source_is_detected_and_tonemapped(hdr, tmp_path):
    assert probe_clip(hdr).hdr is True
    assert _is_hdr(hdr) is True
    filtro = _decode_color_filter(hdr, 360)
    assert "tonemap" in filtro and "bt709" in filtro


def test_sdr_source_keeps_the_simple_matrix_conversion(tmp_path):
    sdr = tmp_path / "sdr.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(sdr),
    )  # fmt: skip
    filtro = _decode_color_filter(sdr, 180)
    assert "tonemap" not in filtro and filtro.startswith("scale=in_color_matrix=")


def test_hdr_render_produces_sdr_without_washed_out_image(hdr, tmp_path):
    project = project_from_files([hdr])
    cam = camera_path(_track(640, 360, 30), 640, 360)
    saida = render_timeline(
        project.timeline, tmp_path / "hdr_out.mp4", size=SAIDA, cameras={0: cam}
    )
    bruto = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(saida)]
        + ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    frame = np.frombuffer(bruto, np.uint8).reshape(1920, 1080).astype(float)
    caixa = frame[900:1020, 480:600].mean()  # quadrado branco, no centro
    fundo = frame[100:200, 480:600].mean()  # cinza ao redor
    # o tonemapping preserva o contraste: o fundo não estoura em branco
    assert fundo < 235 and caixa > fundo + 20


# ------------------------------------------------------------------ fonte grande (4K)


@pytest.fixture(scope="module")
def quatro_k(tmp_path_factory) -> Path:
    """3840x2160, 1 s: fundo preto com um quadrado branco de 400 px no centro."""
    saida = tmp_path_factory.mktemp("4k") / "4k.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=black:s=3840x2160:r=30:d=1",
        "-vf", "drawbox=x=1720:y=880:w=400:h=400:color=white:t=fill",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
        str(saida),
    )  # fmt: skip
    return saida


def test_large_source_is_scaled_before_the_pipe_and_keeps_the_geometry(quatro_k, tmp_path):
    project = project_from_files([quatro_k])
    cam = camera_path(_track(3840, 2160, 30), 3840, 2160)
    assert cam.cw > SAIDA[0]  # a janela 9:16 do 4K é maior que a saída: há redução
    saida = render_timeline(project.timeline, tmp_path / "4k_out.mp4", size=SAIDA, cameras={0: cam})
    bruto = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "0.3", "-i", str(saida)]
        + ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    frame = np.frombuffer(bruto, np.uint8).reshape(1920, 1080).astype(float)
    pesos = np.clip(frame[900:1020] / 255, 0, 1)
    largura = pesos.sum(axis=1).mean()
    centro = (pesos * np.arange(1080)).sum() / pesos.sum()
    # quadrado de 400 px numa janela de 1215 px de largura → ~356 px na saída, centrado
    assert largura == pytest.approx(400 * SAIDA[0] / cam.cw, rel=0.04)
    assert centro == pytest.approx(540, abs=3)
