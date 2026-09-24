"""Parte 5, Etapa 1: segmentos independentes e remontagem real com FFmpeg."""

import json
import subprocess

import cv2
import numpy as np
from PIL import Image

from src.broll.transitions import Cutaway
from src.captions import CaptionStyle, Grupo, build_ass
from src.editing.incremental_render import render_incremental
from src.editing.project_schema import DocumentoEdicao, Elemento, com_plano
from src.images import ItemImagem, Overlay, PlanoImagens
from src.project import Clip, ClipMeta, Timeline
from src.render import render_timeline
from src.transcribe import Palavra

from .conftest import requires_ffmpeg


def _video(path):
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=160x288:r=30:d=6",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        check=True,
        capture_output=True,
    )


def _frames(path):
    cap = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return np.stack(frames)


def _audio(path):
    return subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
            "-i", str(path), "-vn", "-f", "s16le", "-ac", "1", "-ar", "16000", "-",
        ],
        check=True,
        capture_output=True,
    ).stdout


def _timeline(clip):
    meta = ClipMeta(duracao=6, largura=160, altura=288, fps=30, tem_audio=True)
    timeline = Timeline(clipes=[Clip(arquivo=str(clip), trechos=[(0, 6)], meta=meta)])
    timeline.recalcular_offsets()
    return timeline


@requires_ffmpeg
def test_one_image_change_reuses_other_segments_and_matches_clean_render(tmp_path):
    clip = tmp_path / "camera.mp4"
    _video(clip)
    timeline = _timeline(clip)
    plano = PlanoImagens(
        assinatura="teste",
        itens=[
            ItemImagem(
                id=i, indice=i, clipe=0, palavra=f"palavra{i}", query=f"objeto{i}",
                inicio=start, duracao=1,
            )
            for i, start in ((1, 0.5), (2, 4.5))
        ],
    )
    documento = com_plano(DocumentoEdicao(), plano)
    first_png, second_png = tmp_path / "primeira.png", tmp_path / "segunda.png"
    Image.new("RGBA", (80, 80), (255, 0, 0, 255)).save(first_png)
    Image.new("RGBA", (80, 80), (0, 0, 255, 255)).save(second_png)
    overlays = [
        Overlay(first_png, 20, 20, 80, 80, 0.5, 1.5, 1),
        Overlay(second_png, 20, 20, 80, 80, 4.5, 5.5, 2),
    ]
    cache = tmp_path / "cache"
    first = render_incremental(
        timeline, documento, tmp_path / "primeiro.mp4", cache,
        size=(160, 288), overlays=overlays,
    )
    assert first.renderizados == first.segmentos
    before = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))

    Image.new("RGBA", (80, 80), (0, 255, 0, 255)).save(first_png)
    changed = render_incremental(
        timeline, documento, tmp_path / "editado.mp4", cache,
        size=(160, 288), overlays=overlays, ids_alterados={"img_001"},
    )
    after = json.loads((cache / "manifest.json").read_text(encoding="utf-8"))
    assert changed.reutilizados > 0
    assert changed.frames_renderizados < changed.total_frames / 2
    assert all(
        left["hash"] == right["hash"]
        for left, right in zip(before["segmentos"], after["segmentos"], strict=True)
        if "img_001" not in left["ids"]
    )
    clean = render_incremental(
        timeline, documento, tmp_path / "limpo.mp4", tmp_path / "cache_limpocompleto",
        size=(160, 288), overlays=overlays,
    )
    assert clean.renderizados == clean.segmentos
    assert np.array_equal(_frames(changed.output), _frames(clean.output))
    assert _audio(changed.output) == _audio(clean.output)


@requires_ffmpeg
def test_caption_uses_final_timeline_time_in_later_segment(tmp_path):
    clip = tmp_path / "camera.mp4"
    _video(clip)
    timeline = _timeline(clip)
    texto = build_ass(
        [[Grupo([Palavra(indice=0, texto="TESTE", inicio=3.2, fim=3.6)], 3.2, 4.0)]],
        CaptionStyle().for_output((160, 288)), (160, 288),
    )
    ass = tmp_path / "legendas.ass"
    ass.write_text(texto, encoding="utf-8")
    output = tmp_path / "final.mp4"
    result = render_incremental(
        timeline, DocumentoEdicao(), output, tmp_path / "cache",
        size=(160, 288), legendas=ass, max_gap=1.5,
    )
    frames = _frames(output)
    assert result.segmentos > 1
    assert frames[75].max() == 0  # antes da legenda, num segmento posterior
    assert frames[105].max() > 0  # legenda em 3,5 s
    assert frames[135].max() == 0  # depois da legenda

    # Ativar/desativar texto não recodifica trechos sem evento visível.
    sem_legenda = render_incremental(
        timeline, DocumentoEdicao(), tmp_path / "sem_legenda.mp4", tmp_path / "cache",
        size=(160, 288), max_gap=1.5,
    )
    assert sem_legenda.reutilizados >= 3
    com_legenda = render_incremental(
        timeline, DocumentoEdicao(), tmp_path / "com_legenda.mp4", tmp_path / "cache",
        size=(160, 288), legendas=ass, max_gap=1.5,
    )
    assert com_legenda.reutilizados >= 3
    assert np.array_equal(_frames(com_legenda.output), frames)
    assert _audio(com_legenda.output) == _audio(output)


@requires_ffmpeg
def test_inactive_element_does_not_invalidate_cache(tmp_path):
    clip = tmp_path / "camera.mp4"
    _video(clip)
    timeline = _timeline(clip)
    documento = DocumentoEdicao(elementos=[
        Elemento(
            id="img_099", tipo="imagem", inicio=1, fim=2,
            ativo=False, dados={"query": "antiga"},
        )
    ])
    cache = tmp_path / "cache"
    render_incremental(
        timeline, documento, tmp_path / "primeiro.mp4", cache, size=(160, 288),
    )
    documento.elementos[0].dados["query"] = "nova"
    result = render_incremental(
        timeline, documento, tmp_path / "segundo.mp4", cache,
        size=(160, 288), ids_alterados={"img_099"},
    )
    assert result.renderizados == 0
    assert result.reutilizados == result.segmentos
    legacy = render_timeline(timeline, tmp_path / "legado.mp4", size=(160, 288))
    assert _audio(result.output) == _audio(legacy)


@requires_ffmpeg
def test_broll_slide_reuses_unaffected_segments(tmp_path):
    clip = tmp_path / "camera.mp4"
    _video(clip)
    broll = tmp_path / "broll.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=blue:s=160x288:r=30:d=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(broll),
        ],
        check=True, capture_output=True,
    )
    timeline = _timeline(clip)
    documento = DocumentoEdicao(elementos=[
        Elemento(id="broll_001", tipo="broll", inicio=2, fim=3, dados={"query": "azul"})
    ])
    cutaways = [Cutaway(2, 3, broll, 0)]
    cache = tmp_path / "cache"
    initial = render_incremental(
        timeline, documento, tmp_path / "inicial.mp4", cache,
        size=(160, 288), broll=cutaways, broll_transition="slide",
    )
    assert _frames(initial.output)[75, 140, 80, 0] > 100
    documento.elementos[0].dados["query"] = "outro azul"
    changed = render_incremental(
        timeline, documento, tmp_path / "alterado.mp4", cache,
        size=(160, 288), broll=cutaways, broll_transition="slide",
        ids_alterados={"broll_001"},
    )
    assert changed.reutilizados > 0
    assert changed.frames_renderizados < changed.total_frames / 2
    clean = render_incremental(
        timeline, documento, tmp_path / "limpo.mp4", tmp_path / "cache_limpo",
        size=(160, 288), broll=cutaways, broll_transition="slide",
    )
    assert np.array_equal(_frames(changed.output), _frames(clean.output))
    assert _audio(changed.output) == _audio(clean.output)
