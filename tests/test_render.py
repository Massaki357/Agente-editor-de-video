"""Render simples (Etapa 3): trechos → intermediários → concat, com vídeos sintéticos."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.project import Clip, Timeline
from src.render import RenderError, plan_segments, render_timeline
from tests.conftest import make_video, requires_ffmpeg

pytestmark = requires_ffmpeg

FPS = 30
SR = 48_000
FRAME = 1 / FPS


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format"]
        + [str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def _stream(info: dict, kind: str) -> dict:
    return next(s for s in info["streams"] if s["codec_type"] == kind)


def _decode_audio(path: Path) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path)]
        + ["-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64)


def _decode_frames(path: Path, size: tuple[int, int]) -> np.ndarray:
    w, h = size
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path)]
        + ["-map", "0:v:0", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w).astype(np.float64)


def _make_tone(path: Path, duration: float, freq: int = 440) -> Path:
    """Vídeo com seno contínuo de amplitude alta (~0,8 FS), áudio em PCM (sem perdas)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    cmd += ["-f", "lavfi", "-i", f"testsrc2=size=320x180:rate={FPS}:duration={duration}"]
    cmd += ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate={SR}:duration={duration}"]
    cmd += ["-af", "volume=6", "-c:a", "pcm_s16le", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    folder = tmp_path_factory.mktemp("render_src")
    return {
        "a": make_video(folder / "a.mp4", duration=2.0),
        "b": make_video(folder / "b.mp4", duration=2.0, size="640x360"),
        "mudo": make_video(folder / "mudo.mp4", duration=1.0, audio=False),
    }


def _timeline(*clips: tuple[Path, list[tuple[float, float]]]) -> Timeline:
    return Timeline(clipes=[Clip(arquivo=str(p), trechos=t) for p, t in clips])


def test_formato_duracao_e_sincronia(clips, tmp_path):
    trechos_a = [(0.1, 0.7), (1.0, 1.9)]
    trechos_b = [(0.2, 1.2), (1.5, 1.8)]
    tl = _timeline((clips["a"], trechos_a), (clips["b"], trechos_b))
    calls: list[tuple[int, int]] = []

    out = render_timeline(
        tl, tmp_path / "out.mp4", size=(240, 426), progress=lambda d, t: calls.append((d, t))
    )
    info = _probe(out)
    v, a = _stream(info, "video"), _stream(info, "audio")

    assert (v["width"], v["height"]) == (240, 426)
    assert v["avg_frame_rate"] == "30/1"
    assert int(a["sample_rate"]) == SR and a["channels"] == 2

    esperado = sum(f - i for i, f in trechos_a + trechos_b)
    assert int(v["nb_frames"]) == round(esperado * FPS)
    assert float(v["duration"]) == pytest.approx(esperado, abs=FRAME)
    # Garantia de sincronia: os streams terminam juntos.
    assert float(a["duration"]) == pytest.approx(float(v["duration"]), abs=FRAME)
    assert float(info["format"]["duration"]) == pytest.approx(esperado, abs=FRAME)

    assert calls[-1] == (5, 5)  # 4 trechos + concatenação
    assert [d for d, _ in calls] == sorted(d for d, _ in calls)


def test_tamanho_padrao_e_do_primeiro_clipe(clips, tmp_path):
    tl = _timeline((clips["b"], [(0.0, 0.5)]), (clips["a"], [(0.0, 0.5)]))
    info = _probe(render_timeline(tl, tmp_path / "out.mp4"))
    v = _stream(info, "video")
    assert (v["width"], v["height"]) == (640, 360)


def test_resolucoes_diferentes_saem_no_tamanho_pedido(clips, tmp_path):
    tl = _timeline((clips["a"], [(0.0, 0.5)]), (clips["b"], [(0.5, 1.0)]))
    out = render_timeline(tl, tmp_path / "out.mp4", size=(1080, 1920))
    v = _stream(_probe(out), "video")
    assert (v["width"], v["height"]) == (1080, 1920)
    assert v.get("sample_aspect_ratio", "1:1") in ("1:1", "0:1")


def test_clipe_sem_audio_vira_silencio(clips, tmp_path):
    tl = _timeline((clips["mudo"], [(0.0, 0.5)]), (clips["a"], [(0.0, 0.5)]))
    out = render_timeline(tl, tmp_path / "out.mp4", size=(320, 180))
    info = _probe(out)
    a, v = _stream(info, "audio"), _stream(info, "video")
    assert float(a["duration"]) == pytest.approx(float(v["duration"]), abs=FRAME)

    pcm = _decode_audio(out)
    corte = int(0.5 * SR)
    mudo, tom = pcm[: corte - 1000], pcm[corte + 2000 : 2 * corte - 2000]
    assert np.abs(mudo).max() < 50  # silêncio digital (tolerância do AAC)
    assert np.sqrt(np.mean(tom**2)) > 1000


TRECHOS_TOM = [(0.2, 0.9), (1.3666666666666667, 2.0333333333333333), (2.4, 2.9)]


def _rms_nas_emendas(tmp_path: Path, fade: float) -> tuple[list[float], float]:
    tom = _make_tone(tmp_path / "tom.mov", duration=3.0)
    # Cortes em fases arbitrárias do seno de 440 Hz (não múltiplos do período).
    tl = _timeline((tom, TRECHOS_TOM))
    out = render_timeline(tl, tmp_path / "out.mp4", size=(320, 180), fade=fade)
    pcm = _decode_audio(out)

    janela = int(0.003 * SR)
    emendas = np.cumsum([round((f - i) * FPS) / FPS for i, f in TRECHOS_TOM])[:-1]
    rms_tom = float(np.sqrt(np.mean(pcm[int(0.2 * SR) : int(0.5 * SR)] ** 2)))
    rms = []
    for t in emendas:
        c = int(round(t * SR))
        rms.append(float(np.sqrt(np.mean(pcm[c - janela : c + janela] ** 2))))
    return rms, rms_tom


def test_sem_estalos_nas_emendas(tmp_path):
    rms, rms_tom = _rms_nas_emendas(tmp_path, fade=0.025)
    assert rms_tom > 10_000
    assert len(rms) == 2
    for r in rms:
        assert r < 0.2 * rms_tom, f"rms na emenda {r:.0f} vs tom {rms_tom:.0f}"


def test_sem_fade_a_emenda_nao_e_silenciosa(tmp_path):
    """Controle: prova que o teste acima detecta emendas sem fade."""
    rms, rms_tom = _rms_nas_emendas(tmp_path, fade=0.0)
    assert all(r > 0.5 * rms_tom for r in rms)


def test_trecho_comeca_no_frame_certo(clips, tmp_path):
    """O 1º frame de cada trecho é o frame round(inicio*fps) do clipe original."""
    src = _decode_frames(clips["a"], (320, 180))
    trechos = [(0.3, 0.5), (1.0, 1.2)]
    out = render_timeline(_timeline((clips["a"], trechos)), tmp_path / "o.mp4", size=(320, 180))
    frames = _decode_frames(out, (320, 180))
    assert len(frames) == 12

    for k, (inicio, _) in zip((0, 6), trechos, strict=True):
        alvo = round(inicio * FPS)
        erros = {j: np.mean((frames[k] - src[j]) ** 2) for j in (alvo - 1, alvo, alvo + 1)}
        assert min(erros, key=erros.get) == alvo, erros


def test_trechos_arredondados_e_vazios_descartados(clips):
    from src.clips import probe_clip

    tl = _timeline((clips["a"], [(0.1001, 0.1101), (0.2, 0.7004)]))
    segs = plan_segments(tl, [probe_clip(clips["a"])], FPS)
    assert len(segs) == 1
    assert segs[0].frames == 15 and segs[0].inicio == pytest.approx(0.2)


def test_work_dir_com_espacos_acentos_e_apostrofo(clips, tmp_path):
    work = tmp_path / "pasta d'água ção"
    tl = _timeline((clips["a"], [(0.0, 0.3)]), (clips["b"], [(0.0, 0.3)]))
    out = render_timeline(tl, tmp_path / "saída final.mp4", size=(320, 180), work_dir=work)
    assert int(_stream(_probe(out), "video")["nb_frames"]) == 18
    assert (work / "lista.txt").exists()


def test_arquivo_invalido_gera_render_error(tmp_path):
    ruim = tmp_path / "ruim.mp4"
    ruim.write_bytes(b"isto nao e um video")
    with pytest.raises(RenderError):
        render_timeline(_timeline((ruim, [(0.0, 0.5)])), tmp_path / "out.mp4")


def test_ffmpeg_falha_inclui_stderr(clips, tmp_path):
    from src.clips import probe_clip

    meta = probe_clip(clips["a"])
    tl = Timeline(
        clipes=[Clip(arquivo=str(tmp_path / "sumiu.mp4"), trechos=[(0.0, 0.5)], meta=meta)]
    )
    with pytest.raises(RenderError, match="(?s)FFmpeg falhou.*sumiu"):
        render_timeline(tl, tmp_path / "out.mp4", size=(320, 180))


def test_cli_renderiza_project_json(clips, tmp_path):
    from src.project import Project
    from src.render import main

    projeto = tmp_path / "project.json"
    Project(timeline=_timeline((clips["a"], [(0.0, 0.4)]), (clips["b"], [(0.0, 0.4)]))).salvar(
        projeto
    )
    out = tmp_path / "saida.mp4"
    assert main([str(projeto), "-o", str(out), "--size", "180x320"]) == 0
    v = _stream(_probe(out), "video")
    assert (v["width"], v["height"]) == (180, 320) and int(v["nb_frames"]) == 24
