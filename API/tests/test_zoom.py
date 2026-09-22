"""Etapa 9: zooms no rosto (plano, geometria da janela e render)."""

import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.clips import project_from_files
from src.cuts import TimeMap
from src.face import FaceTrack
from src.images import PalavraGlobal, ZoomPlanParams, validate_zooms
from src.pipeline import face_boxes_fn
from src.project import Clip, Timeline
from src.reframe import ZoomParams, camera_path, plan_zooms, zoom_curve
from src.render import _crop, _crop_subpixel, render_timeline
from src.transcribe import Palavra

from .conftest import requires_ffmpeg

FPS = 30
SAIDA = (1080, 1920)


def rosto(
    n: int = 90,
    cx=0.5,
    cy=0.4,
    w: float = 0.1,
    h: float = 0.18,
    detectado: bool = True,
    largura: int = 1280,
    altura: int = 720,
) -> FaceTrack:
    """Rastreio sintético: `cx`/`cy` podem ser listas (rosto em movimento)."""
    cxs = list(cx) if isinstance(cx, list | tuple | np.ndarray) else [cx] * n
    cys = list(cy) if isinstance(cy, list | tuple | np.ndarray) else [cy] * len(cxs)
    n = len(cxs)
    caixa = [[a, b, w, h] if detectado else None for a, b in zip(cxs, cys, strict=True)]
    return FaceTrack(
        fps=FPS, largura=largura, altura=altura, cx=cxs, cy=cys, w=[w] * n, h=[h] * n,
        detectado=[detectado] * n, caixa=caixa,
    )  # fmt: skip


def _dentro(caixa, janela, largura=1280, altura=720) -> bool:
    cx, cy, w, h = caixa
    x, y, zw, zh = janela
    return (
        x - 1e-6 <= (cx - w / 2) * largura
        and (cx + w / 2) * largura <= x + zw + 1e-6
        and y - 1e-6 <= (cy - h / 2) * altura
        and (cy + h / 2) * altura <= y + zh + 1e-6
    )


# ------------------------------------------------------------------ curva


def test_zoom_curve_ramps_smoothly_and_returns_to_one():
    escala = zoom_curve([(1.0, 3.0, 1.15)], rampa=0.35)
    t = np.arange(0, 4, 1 / FPS)
    s = np.array([escala(x) for x in t])
    assert s[t < 1.0].max() == 1.0 and s[t > 3.0].max() == 1.0
    assert s.max() == pytest.approx(1.15)
    assert np.all((s >= 1.0) & (s <= 1.15 + 1e-9))
    # sem salto nem tranco: variação por frame e "aceleração" pequenas
    assert np.abs(np.diff(s)).max() < 0.15 * 1.5 / (0.35 * FPS) + 1e-3
    assert np.abs(np.diff(s, 2)).max() < 0.01
    # sobe até o pico e fica nele: nunca oscila no meio
    meio = s[(t > 1.4) & (t < 2.6)]
    assert np.allclose(meio, 1.15)


def test_short_zoom_shrinks_the_ramps_to_fit():
    escala = zoom_curve([(0.0, 0.4, 1.15)], rampa=0.35)
    assert escala(0.2) == pytest.approx(1.15)
    assert escala(0.0) == escala(0.4) == 1.0


# ------------------------------------------------------------------ janela


def test_zoom_window_shrinks_around_the_face_and_stays_in_frame():
    cam = camera_path(rosto(), 1280, 720)
    base = cam.window_f(1.0)
    x, y, zw, zh = cam.window_f(1.0, 1.15)
    assert zw == pytest.approx(cam.cw / 1.15) and zh == pytest.approx(cam.ch / 1.15)
    assert zw / zh == pytest.approx(cam.cw / cam.ch)  # mesmo aspecto: 9:16
    assert x + zw / 2 == pytest.approx(base[0] + base[2] / 2)  # mesmo centro horizontal
    # rosto (cy=0,4 → 288 px) a ~40% da janela
    assert (288 - y) / zh == pytest.approx(0.4, abs=0.01)
    assert 0 <= x and x + zw <= 1280 and 0 <= y and y + zh <= 720
    assert _dentro((0.5, 0.4, 0.1, 0.18), (x, y, zw, zh))
    assert cam.window_f(1.0, 1.0) == base  # escala 1: exatamente a janela da Etapa 6


@pytest.mark.parametrize("cx", [0.01, 0.99])
def test_zoom_near_the_frame_edge_never_leaves_the_frame(cx):
    cam = camera_path(rosto(cx=cx, w=0.05), 1280, 720)
    for t in np.linspace(0, 2.9, 30):
        x, y, zw, zh = cam.window_f(t, 1.15)
        assert -1e-6 <= x and x + zw <= 1280 + 1e-6 and -1e-6 <= y and y + zh <= 720 + 1e-6


def test_zoom_keeps_a_face_high_in_the_frame_whole():
    # rosto grande e alto: centrar a 40% o cortaria em cima; a janela desce o mínimo
    cam = camera_path(rosto(cy=0.2, w=0.12, h=0.3), 1280, 720)
    janela = cam.window_f(1.0, 1.15)
    assert _dentro((0.5, 0.2, 0.12, 0.3), janela)


def test_zoom_window_moves_without_jumps_while_face_and_scale_change():
    cx = np.concatenate([np.full(30, 0.3), np.linspace(0.3, 0.7, 60), np.full(60, 0.7)])
    cam = camera_path(rosto(cx=cx), 1280, 720)
    escala = zoom_curve([(0.5, 4.5, 1.15)])
    t = np.arange(0, 5, 1 / FPS)
    j = np.array([cam.window_f(x, escala(x)) for x in t])
    base = np.array([cam.window_f(x) for x in t])  # câmera da Etapa 6, sem zoom
    for col in range(4):
        # px do quadro de origem por frame: o zoom não cria saltos nem trancos além
        # dos da própria câmera (que acelera de uma vez ao começar a seguir o rosto)
        assert np.abs(np.diff(j[:, col])).max() < 12
        assert np.abs(np.diff(j[:, col], 2)).max() <= np.abs(np.diff(base[:, col], 2)).max() + 3.5


# ------------------------------------------------------------------ limite pelo rosto


def _to_src(t_out: float) -> tuple[int, float]:
    return 0, t_out


def test_plan_zooms_uses_the_full_scale_for_a_normal_face():
    cams = {0: camera_path(rosto(), 1280, 720)}
    assert plan_zooms([(0.5, 2.0)], _to_src, cams) == [(0.5, 2.0, 1.15)]


def test_plan_zooms_limits_the_peak_so_a_big_face_is_never_cut():
    # rosto largo: com 35% de folga, não cabe zoom de 1,15; o pico é menor
    track = rosto(w=0.2165, h=0.3)  # 69% da largura da janela 9:16
    cam = camera_path(track, 1280, 720)
    ((_, _, pico),) = plan_zooms([(0.5, 2.0)], _to_src, {0: cam})
    limite = cam.face_zoom_limit(1.0)
    assert pico == pytest.approx(limite, abs=1e-3)
    assert 1.02 <= pico < 1.15
    for t in np.arange(0.5, 2.0, 1 / FPS):  # rosto real inteiro em todo frame do zoom
        assert _dentro(track.box_at(t), cam.window_f(t, pico))


def test_plan_zooms_uses_the_worst_frame_of_the_interval():
    # o rosto cresce no meio do intervalo: o pico vale para o intervalo todo (sem oscilar)
    track = rosto()
    track.caixa[45] = [0.5, 0.4, 0.22, 0.3]
    cam = camera_path(track, 1280, 720)
    ((_, _, pico),) = plan_zooms([(1.0, 2.0)], _to_src, {0: cam})
    assert pico == pytest.approx(cam.face_zoom_limit(45 / FPS), abs=1e-3)
    assert plan_zooms([(0.0, 1.0)], _to_src, {0: cam})[0][2] == 1.15  # fora do frame 45


def test_plan_zooms_without_detection_follows_the_camera_and_skips_without_reframe():
    # detector perdeu o rosto: o zoom segue o caminho da câmera (última posição)
    sem_rosto = {0: camera_path(rosto(detectado=False), 1280, 720)}
    assert plan_zooms([(0.5, 2.0)], _to_src, sem_rosto) == [(0.5, 2.0, 1.15)]
    assert plan_zooms([(0.5, 2.0)], _to_src, {}) == []  # clipe sem reenquadramento
    enorme = {0: camera_path(rosto(w=0.3, h=0.9), 1280, 720)}
    assert plan_zooms([(0.5, 2.0)], _to_src, enorme) == []  # zoom não caberia


# ------------------------------------------------------------------ validação do plano

Z = ZoomPlanParams()


def _timeline() -> Timeline:
    t = Timeline(
        clipes=[
            Clip(arquivo="a.mp4", trechos=[(0.0, 10.0)]),  # t_out 0–10
            Clip(arquivo="b.mp4", trechos=[(0.0, 10.0)]),  # t_out 10–20
        ]
    )
    t.recalcular_offsets()
    return t


def _palavras(*itens) -> list[PalavraGlobal]:
    return [
        PalavraGlobal(i, clipe, Palavra(indice=i, texto=texto, inicio=t, fim=t + 0.3))
        for i, (texto, t, clipe) in enumerate(itens)
    ]


PALAVRAS = _palavras(
    ("isso", 1.0, 0), ("muda", 3.0, 0), ("tudo", 8.0, 0), ("fim", 9.6, 0),
    ("agora", 10.05, 1), ("sim", 12.0, 1), ("nunca", 19.5, 1),
)  # fmt: skip


def test_zoom_starts_slightly_before_the_word_with_clamped_duration():
    tm = TimeMap(_timeline())
    (z,) = validate_zooms([(0, "isso", 9.0)], PALAVRAS, tm, Z)
    assert z.inicio == pytest.approx(0.85) and z.duracao == pytest.approx(2.5)
    (z,) = validate_zooms([(0, "isso", 0.2)], PALAVRAS, tm, Z)
    assert z.duracao == pytest.approx(1.0)
    assert z.ativo and z.clipe == 0 and z.palavra == "isso"


def test_at_most_one_zoom_every_eight_seconds():
    tm = TimeMap(_timeline())
    zooms = validate_zooms(
        [(0, "isso", 1.5), (1, "muda", 1.5), (2, "tudo", 1.5), (5, "sim", 1.5)], PALAVRAS, tm, Z
    )
    assert [z.palavra for z in zooms] == ["isso", "sim"]  # "muda" 2 s depois; "tudo" 7 s
    inicios = [z.inicio for z in zooms]
    assert all(b - a >= 8.0 for a, b in zip(inicios, inicios[1:], strict=False))


def test_zoom_never_crosses_a_seam():
    tm = TimeMap(_timeline())
    (z,) = validate_zooms([(2, "tudo", 2.5)], PALAVRAS, tm, Z)
    assert z.fim <= 10.0 + 1e-9  # cortado na emenda
    assert validate_zooms([(3, "fim", 2.0)], PALAVRAS, tm, Z) == []  # sobraria 0,4 s
    (z,) = validate_zooms([(4, "agora", 1.5)], PALAVRAS, tm, Z)
    assert z.inicio == pytest.approx(10.0)  # não começa antes da emenda
    assert validate_zooms([(6, "nunca", 2.0)], PALAVRAS, tm, Z) == []  # fim do vídeo


def test_zoom_with_wrong_index_is_fixed_or_dropped():
    tm = TimeMap(_timeline())
    (z,) = validate_zooms([(0, "muda", 1.5)], PALAVRAS, tm, Z)
    assert z.indice == 1  # o LLM errou por 1
    assert validate_zooms([(0, "elefante", 1.5)], PALAVRAS, tm, Z) == []
    assert validate_zooms([(99, "isso", 1.5)], PALAVRAS, tm, Z) == []


# ------------------------------------------------------------------ render


def test_subpixel_crop_matches_the_integer_crop_on_integer_windows():
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    frame = np.asarray(
        __import__("cv2").GaussianBlur(frame, (0, 0), 3), dtype=np.uint8
    )  # imagem suave: diferenças só de interpolação
    a = _crop(frame, 438, 0, 404, 720, 1080, 1920).astype(float)
    b = _crop_subpixel(frame, (438.0, 0.0, 404.0, 720.0), 1080, 1920).astype(float)
    assert np.abs(a - b)[50:-50, 50:-50].mean() < 1.0


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def quadrado(tmp_path_factory) -> Path:
    """1280x720, 2 s, fundo preto com um quadrado branco de 100 px no centro."""
    out = tmp_path_factory.mktemp("zoom") / "quadrado.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-vf", "drawbox=x=590:y=310:w=100:h=100:color=white:t=fill",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out),
    )  # fmt: skip
    return out


def _faixa(video: Path, linha: int, altura: int = 20) -> np.ndarray:
    """Luminância média de uma faixa horizontal de cada frame → (frames, 1080)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(video)]
        + ["-vf", f"crop=1080:{altura}:0:{linha - altura // 2}", "-f", "rawvideo"]
        + ["-pix_fmt", "gray", "-"],
        check=True,
        capture_output=True,
    )
    frames = np.frombuffer(proc.stdout, np.uint8).reshape(-1, altura, 1080)
    return frames.astype(float).mean(axis=1)


@requires_ffmpeg
def test_render_zoom_is_smooth_centered_and_reaches_the_scale(quadrado, tmp_path):
    project = project_from_files([quadrado])
    track = rosto(n=60, cx=0.5, cy=0.5, w=0.08, h=0.14)
    cam = camera_path(track, 1280, 720)
    escala = zoom_curve([(0.3, 1.7, 1.15)])
    out = render_timeline(
        project.timeline, tmp_path / "zoom.mp4", size=SAIDA, cameras={0: cam}, zoom=escala
    )
    linhas = _faixa(out, 880)  # atravessa o quadrado com e sem zoom
    pesos = np.clip(linhas / 255.0, 0, 1)
    largura = pesos.sum(axis=1)
    centro = (pesos * np.arange(1080)).sum(axis=1) / largura
    base = 100 * 1080 / cam.cw  # ~267 px sem zoom
    assert len(largura) == 60
    assert largura[:6].mean() == pytest.approx(base, rel=0.03)
    assert largura[30] == pytest.approx(base * 1.15, rel=0.03)  # pico
    assert largura[-6:].mean() == pytest.approx(base, rel=0.03)  # voltou
    # sem salto nem tremor: o quadrado cresce e encolhe suavemente, sempre no centro
    assert np.abs(np.diff(largura)).max() < 8
    assert np.abs(np.diff(largura, 2)).max() < 4
    assert np.abs(centro - 540).max() < 1.0


@requires_ffmpeg
def test_image_avoids_the_zoomed_face(quadrado):
    """Com zoom, a caixa do rosto na saída é a ampliada (as imagens desviam dela)."""
    project = project_from_files([quadrado])
    project.timeline.substituir_trechos(0, [(0.0, 2.0)])
    track = rosto(n=60, cx=0.5, cy=0.5, w=0.08, h=0.14)
    cams = {0: camera_path(track, 1280, 720)}
    zooms = plan_zooms([(0.3, 1.7)], TimeMap(project.timeline).to_src, cams)
    escala = zoom_curve(zooms)
    sem = face_boxes_fn(project, {0: track}, cams, SAIDA)(0.9, 1.1)
    com = face_boxes_fn(project, {0: track}, cams, SAIDA, zoom=escala)(0.9, 1.1)
    assert len(sem) == len(com) > 0
    for (x0, _y0, w0, h0), (x1, _y1, w1, h1) in zip(sem, com, strict=True):
        assert w1 == pytest.approx(w0 * 1.15, abs=2) and h1 == pytest.approx(h0 * 1.15, abs=2)
        assert x1 <= x0 and x1 + w1 >= x0 + w0  # cresce ao redor do centro
    # fora do zoom, as caixas são as mesmas
    assert face_boxes_fn(project, {0: track}, cams, SAIDA, zoom=escala)(0.0, 0.2) == (
        face_boxes_fn(project, {0: track}, cams, SAIDA)(0.0, 0.2)
    )


def test_zoom_params_defaults_match_the_plan():
    assert ZoomParams().escala == 1.15
    assert Z.intervalo_min == 8.0 and (Z.duracao_min, Z.duracao_max) == (1.0, 2.5)
