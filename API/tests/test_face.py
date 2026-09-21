"""Etapa 5: rastreio de rosto.

Os testes com detecção real usam o modelo BlazeFace e uma foto de teste pública do
MediaPipe, baixados uma vez para `.cache/test-assets/` (pulados se não houver rede).
Vídeos sintéticos com trajetória conhecida permitem medir o erro e o tremor.
"""

import subprocess
from pathlib import Path

import numpy as np
import pytest
import requests

import src.face as face
from src.config import PROJECT_ROOT, Settings, get_settings
from src.face import (
    CENTRO,
    FaceParams,
    alpha_por_fps,
    ema_bidirecional,
    fill_and_interpolate,
    pick_face,
    track_faces,
    zona_morta,
)

ASSETS = PROJECT_ROOT / ".cache" / "test-assets"
PORTRAIT_URL = "https://storage.googleapis.com/mediapipe-assets/portrait.jpg"
# Rosto na foto original 820x1024 (detecção do BlazeFace): centro (400, 232)
FACE_CX, FACE_CY = 400.0, 232.0


# ------------------------------------------------------------------ funções puras


def test_pick_face_prefers_largest_and_central():
    grande_na_borda = (0.9, 0.5, 0.3, 0.4)
    pequeno_no_centro = (0.5, 0.5, 0.1, 0.12)
    assert pick_face([pequeno_no_centro, grande_na_borda]) == grande_na_borda
    parecidos = [(0.85, 0.5, 0.2, 0.2), (0.5, 0.5, 0.2, 0.2)]
    assert pick_face(parecidos) == (0.5, 0.5, 0.2, 0.2)
    assert pick_face([]) is None


def test_fill_centers_before_first_face_and_holds_after_losing_it():
    a = (0.3, 0.4, 0.2, 0.2)
    b = (0.4, 0.4, 0.2, 0.2)
    amostras = [None, None, a, b, None, None, None]  # passo 2 → frames 0..13
    valores, detectado = fill_and_interpolate(amostras, 2, 14)
    assert tuple(valores[0, :2]) == CENTRO and not detectado[0]
    assert tuple(valores[0, 2:]) == (0.2, 0.2)  # tamanho da 1ª detecção, não zero
    assert valores[4, 0] == pytest.approx(0.3) and detectado[4]
    assert valores[5, 0] == pytest.approx(0.35)  # interpolado entre as amostras
    assert valores[13, 0] == pytest.approx(0.4)  # perdeu o rosto: mantém a última posição
    assert not detectado[13]


def test_fill_without_any_face_is_all_center():
    valores, detectado = fill_and_interpolate([None] * 5, 2, 10)
    assert np.allclose(valores[:, :2], CENTRO) and not detectado.any()


def test_bidirectional_ema_has_no_lag_on_a_ramp():
    rampa = np.linspace(0.2, 0.8, 300)
    suave = ema_bidirecional(rampa, 0.1)
    meio = slice(50, 250)
    assert np.max(np.abs(suave[meio] - rampa[meio])) < 0.005


def test_dead_zone_ignores_small_noise_and_follows_real_moves():
    rng = np.random.default_rng(1)
    parado = 0.5 + rng.uniform(-0.01, 0.01, 200)
    assert np.ptp(zona_morta(parado, 0.02)) <= 0.02
    salto = np.concatenate([np.full(50, 0.3), np.full(50, 0.6)])
    out = zona_morta(salto, 0.02)
    assert out[-1] == pytest.approx(0.58)  # segue, ficando dentro da zona
    assert np.max(np.abs(np.diff(out))) <= 0.3


def test_alpha_scales_with_fps():
    assert alpha_por_fps(0.12, 30) == pytest.approx(0.12)
    assert alpha_por_fps(0.12, 60) < 0.12  # mais frames → passo menor por frame


# ------------------------------------------------------------------ com detecção real


def _baixar(url: str, destino: Path) -> Path:
    if not destino.exists():
        destino.parent.mkdir(parents=True, exist_ok=True)
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        destino.write_bytes(resp.content)
    return destino


@pytest.fixture(scope="session")
def assets() -> dict[str, Path]:
    try:
        portrait = _baixar(PORTRAIT_URL, ASSETS / "portrait.jpg")
        modelo = _baixar(face.MODEL_URL, ASSETS / face.MODEL_NAME)
    except requests.RequestException as exc:
        pytest.skip(f"sem rede para baixar os assets de teste: {exc}")
    return {"portrait": portrait, "modelo": modelo}


@pytest.fixture
def settings_com_modelo(assets, isolated_cache) -> Settings:
    """CACHE_DIR isolado, mas com o modelo já presente (sem baixar de novo)."""
    modelo = isolated_cache / "models" / face.MODEL_NAME
    modelo.parent.mkdir(parents=True, exist_ok=True)
    modelo.write_bytes(assets["modelo"].read_bytes())
    return Settings(cache_dir=get_settings().cache_dir)


ALTURA_FOTO = 600  # rosto com ~140 px de altura num quadro de 720 (enquadramento de fala)
ESCALA = ALTURA_FOTO / 1024


def _video(path: Path, portrait: Path, grafo: str, dur: float = 5.0) -> Path:
    """Fundo cinza 1280x720 a 30 fps; `grafo` usa [bg] (fundo) e [p] (foto) e termina em [v]."""
    filtro = f"[1:v]scale=-2:{ALTURA_FOTO}[p];[0:v]null[bg];{grafo}"
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    cmd += ["-f", "lavfi", "-i", f"color=c=gray:s=1280x720:r=30:d={dur}", "-loop", "1"]
    cmd += ["-i", str(portrait), "-filter_complex", filtro, "-map", "[v]", "-t", str(dur)]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture(scope="session")
def moving_video(assets, tmp_path_factory) -> Path:
    # sem rosto no 1º segundo; depois a foto anda de x=100 a x=780 em 4 s
    return _video(
        tmp_path_factory.mktemp("face") / "andando.mp4",
        assets["portrait"],
        "[bg][p]overlay=x='60+(t-1)*170':y=60:enable='gte(t,1)'[v]",
    )


def _gt_cx(t: float) -> float:
    return (60 + (t - 1) * 170 + FACE_CX * ESCALA) / 1280


def test_moving_face_is_tracked_closely(moving_video, settings_com_modelo):
    track = track_faces(moving_video, settings=settings_com_modelo)
    assert (track.largura, track.altura) == (1280, 720)
    assert track.n_frames == pytest.approx(150, abs=2)
    # Depois da transição centro → rosto (~0,5 s de suavização), o erro fica no máximo
    # na zona morta (2% da largura) mais o atraso residual da EMA.
    for t in (2.0, 2.5, 3.0, 3.5, 4.0, 4.4):
        cx, cy, w, _ = track.at(t)
        assert cx == pytest.approx(_gt_cx(t), abs=0.03), t
        assert cy == pytest.approx((60 + FACE_CY * ESCALA) / 720, abs=0.03)
        assert w == pytest.approx(234 * ESCALA / 1280, rel=0.25)


def test_clip_starting_without_face_is_centered(moving_video, settings_com_modelo):
    track = track_faces(moving_video, settings=settings_com_modelo)
    assert not any(track.detectado[:25])
    assert track.at(0.2)[0] == pytest.approx(0.5, abs=0.01)
    assert all(track.detectado[40:140])


def test_track_is_smooth_without_jumps(moving_video, settings_com_modelo):
    track = track_faces(moving_video, settings=settings_com_modelo)
    cx = np.array(track.cx[60:140])  # movimento constante, longe da transição
    velocidade = np.diff(cx)
    assert np.max(np.abs(np.diff(velocidade))) < 0.001  # aceleração ~0: sem tremor
    assert np.all(velocidade >= 0)  # nunca volta para trás
    transicao = np.diff(np.array(track.cx[:60]))
    assert np.max(np.abs(transicao)) < 0.02  # a entrada do rosto não é um salto


def test_camera_shake_is_absorbed(assets, settings_com_modelo, tmp_path):
    video = _video(
        tmp_path / "tremendo.mp4", assets["portrait"], "[bg][p]overlay=x='440+4*sin(t*37)':y=60[v]"
    )
    track = track_faces(video, settings=settings_com_modelo)
    bruto = np.array([b[0] for b in track.bruto if b])
    assert np.ptp(bruto) > 0.004  # a detecção bruta treme
    assert np.ptp(track.cx[15:-15]) < 0.25 * np.ptp(bruto)


def test_two_faces_picks_the_larger(assets, settings_com_modelo, tmp_path):
    grafo = (
        "[p]split[a][b];[b]scale=-2:300[pq];"
        "[bg][a]overlay=x=760:y=60[t];[t][pq]overlay=x=330:y=300[v]"
    )
    video = _video(tmp_path / "dois.mp4", assets["portrait"], grafo)
    track = track_faces(video, settings=settings_com_modelo)
    assert track.at(2.0)[0] == pytest.approx((760 + FACE_CX * ESCALA) / 1280, abs=0.03)


def test_second_run_uses_cache(moving_video, settings_com_modelo, monkeypatch):
    first = track_faces(moving_video, settings=settings_com_modelo)

    def boom(*a, **k):
        raise AssertionError("não deveria detectar de novo")

    monkeypatch.setattr(face, "detect_raw", boom)
    assert track_faces(moving_video, settings=settings_com_modelo) == first


def test_changing_params_invalidates_cache(moving_video, settings_com_modelo):
    a = track_faces(moving_video, FaceParams(passo=2), settings=settings_com_modelo)
    b = track_faces(moving_video, FaceParams(passo=3), settings=settings_com_modelo)
    assert len(a.bruto) != len(b.bruto)


def test_video_without_any_face(settings_com_modelo, tmp_path):
    from tests.conftest import make_video

    track = track_faces(
        make_video(tmp_path / "sem.mp4", duration=1.0), settings=settings_com_modelo
    )
    assert track.cobertura == 0 and track.at(0.5)[:2] == pytest.approx(CENTRO)


def test_debug_video_is_rendered(moving_video, settings_com_modelo, tmp_path):
    track = track_faces(moving_video, settings=settings_com_modelo)
    out = face.render_debug(moving_video, tmp_path / "debug.mp4", track, 2)
    from src.clips import probe_clip

    meta = probe_clip(out)
    assert (meta.largura, meta.altura) == (1280, 720)
    assert meta.duracao == pytest.approx(5.0, abs=0.1)


@pytest.mark.parametrize(
    ("largura", "altura", "esperado"),
    [
        (1280, 720, [(0, 0, 720), (280, 0, 720), (560, 0, 720)]),
        (720, 1280, [(0, 0, 720), (0, 280, 720), (0, 560, 720)]),
        (500, 500, [(0, 0, 500)]),
    ],
)
def test_square_crops_cover_the_frame(largura, altura, esperado):
    assert face.square_crops(largura, altura) == esperado


def test_last_frames_after_final_sample_are_marked_detected():
    _, detectado = fill_and_interpolate([(0.5, 0.5, 0.2, 0.2)] * 5, 2, 10)
    assert detectado.all()


def test_face_boxes_only_where_detected():
    valores = np.tile([0.4, 0.5, 0.2, 0.2], (10, 1))
    detectado = np.array([False, False, True, True, True, False, False, True, True, False])
    caixas = face.face_boxes(valores, detectado, 0.4)
    assert caixas[0] is None and caixas[5] is None and caixas[9] is None
    assert caixas[3] == pytest.approx([0.4, 0.5, 0.2, 0.2])


def test_box_size_is_right_as_soon_as_face_appears(moving_video, settings_com_modelo):
    track = track_faces(moving_video, settings=settings_com_modelo)
    primeiro = track.detectado.index(True)
    w_bruto = next(b[2] for b in track.bruto if b)
    for i in (primeiro, primeiro + 5, primeiro + 15):
        assert track.caixa[i][2] == pytest.approx(w_bruto, rel=0.25)
        assert track.w[i] == pytest.approx(w_bruto, rel=0.25)  # câmera também


def test_face_box_follows_the_face_without_anticipation(moving_video, settings_com_modelo):
    track = track_faces(moving_video, settings=settings_com_modelo)
    assert track.box_at(0.5) is None  # ainda sem rosto: nenhuma caixa
    for t in (1.2, 2.0, 3.0, 4.0, 4.8):
        cx, cy, _, _ = track.box_at(t)
        assert cx == pytest.approx(_gt_cx(t), abs=0.012), t
        assert cy == pytest.approx((60 + FACE_CY * ESCALA) / 720, abs=0.02), t
