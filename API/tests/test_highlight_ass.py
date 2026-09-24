"""Parte 4, Etapa 1: revelação, permanência e limites das legendas de destaque."""

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.highlight_captions.ass_builder import (
    HighlightStyle,
    build_highlight_ass,
    write_highlight_ass,
    write_highlights_project,
)
from src.highlight_captions.planner import ItemDestaque
from src.images import PalavraGlobal, PlanoImagens, timeline_signature
from src.pipeline import PipelineOptions, render_project
from src.project import Clip, ClipMeta, Project, Timeline
from src.render import render_timeline
from src.transcribe import Palavra

from .conftest import requires_ffmpeg


def _words(*rows: tuple[str, float, float, int, int]) -> list[PalavraGlobal]:
    return [
        PalavraGlobal(i, clip, Palavra(indice=i, texto=text, inicio=start, fim=end), segment)
        for i, (text, start, end, clip, segment) in enumerate(rows)
    ]


def _item(first: int, last: int, words: list[PalavraGlobal], clip: int, segment: int):
    selected = words[first : last + 1]
    return ItemDestaque(
        id=first,
        clipe=clip,
        segmento=segment,
        trecho_inicio_palavra=first,
        trecho_fim_palavra=last,
        inicio=selected[0].palavra.inicio,
        fim=selected[-1].palavra.fim,
        texto=" ".join(w.palavra.texto for w in selected),
        motivo="dado forte",
    )


def _events(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def test_ass_reveals_exact_words_then_holds_full_phrase_with_fade():
    words = _words(
        ("A", 0.5, 0.8, 0, 0),
        ("água", 0.9, 1.2, 0, 0),
        ("mudou.", 1.3, 1.7, 0, 0),
    )
    ass = build_highlight_ass([_item(0, 2, words, 0, 0)], words, [(0, 4)])
    events = _events(ass)
    assert len(events) == 4
    assert [re.findall(r"\d:\d\d:\d\d\.\d\d", event) for event in events] == [
        ["0:00:00.50", "0:00:00.90"],
        ["0:00:00.90", "0:00:01.30"],
        ["0:00:01.30", "0:00:01.70"],
        ["0:00:01.70", "0:00:02.90"],
    ]
    assert "água" not in events[0] and "mudou." not in events[1]
    assert all(word in events[2] for word in ("A", "água", "mudou."))
    assert all(word in events[3] for word in ("A", "água", "mudou."))
    assert r"\fad(0,250)" in events[3] and r"\fad" not in events[2]
    assert "Style: Destaque,Poppins" in ass and ",8,80,80,0,1" in ass
    assert r"\an8\pos(540," in events[0]
    assert "Style: Legenda" not in ass  # estilo separado da legenda contínua


def test_individual_hold_controls_visual_interval_and_occupied_boxes():
    words = _words(("água", 0.5, 0.8, 0, 0), ("mudou.", 0.9, 1.2, 0, 0))
    item = _item(0, 1, words, 0, 0)
    item.duracao_permanencia = 0.4
    intervals = []
    ass = build_highlight_ass(
        [item], words, [(0, 3)], caixas_ocupadas=lambda a, b: intervals.append((a, b)) or []
    )
    assert intervals == [(0.5, 1.6)]
    assert "0:00:01.20,0:00:01.60" in ass


def test_hold_is_complete_when_it_fits_and_rejected_at_seams():
    words = _words(
        ("A", 0.5, 0.8, 0, 0),
        ("água", 0.9, 1.2, 0, 0),
        ("mudou.", 1.3, 1.7, 0, 0),
        ("Tudo", 2.2, 2.5, 0, 1),
        ("melhorou.", 2.6, 3.0, 0, 1),
        ("Há", 4.2, 4.5, 1, 2),
        ("resultado.", 4.6, 5.0, 1, 2),
    )
    first = _item(0, 2, words, 0, 0)
    with pytest.raises(ValueError, match="sem espaço para permanência"):
        build_highlight_ass([first], words, [(0, 2), (2, 4), (4, 6)])
    boundary_word = words[:3].copy()
    boundary_word[-1] = PalavraGlobal(
        2, 0, Palavra(indice=2, texto="mudou.", inicio=1.3, fim=2.0), 0
    )
    with pytest.raises(ValueError, match="sem espaço para permanência"):
        build_highlight_ass([_item(0, 2, boundary_word, 0, 0)], boundary_word, [(0, 2)])

    ass = build_highlight_ass([first], words, [(0, 2.9), (2.9, 6)])
    assert "0:00:01.70,0:00:02.90" in ass
    assert r"\fad(0,250)" in _events(ass)[-1]
    with pytest.raises(ValueError, match="próximo destaque"):
        short_words = [
            *words[:3],
            *[PalavraGlobal(p.indice, p.clipe, p.palavra, 0) for p in words[3:5]],
        ]
        second_item = _item(3, 4, short_words, 0, 0)
        build_highlight_ass([first, second_item], short_words, [(0, 4)])


def test_builder_rejects_stale_text_missing_words_and_crossed_seam():
    words = _words(("A", 0.5, 0.8, 0, 0), ("frase", 0.9, 1.5, 0, 0))
    item = _item(0, 1, words, 0, 0)
    with pytest.raises(ValueError, match="transcrição"):
        build_highlight_ass([item.model_copy(update={"texto": "Frase reescrita"})], words, [(0, 2)])
    with pytest.raises(ValueError, match="emenda"):
        build_highlight_ass([item], words, [(0, 1)])
    with pytest.raises(ValueError, match="ausente"):
        build_highlight_ass([item], words[:1], [(0, 2)])
    assert (
        _events(build_highlight_ass([item.model_copy(update={"ativo": False})], words, [(0, 2)]))
        == []
    )


def test_long_phrase_is_rejected_and_short_text_uses_smaller_font():
    text = "Cada litro economizado virou mais um dia de segurança para todas as famílias."
    tokens = text.split()
    words = _words(
        *[(word, 0.2 + i * 0.28, 0.43 + i * 0.28, 0, 0) for i, word in enumerate(tokens)]
    )
    with pytest.raises(ValueError, match="máximo de 5 palavras"):
        build_highlight_ass([_item(0, len(words) - 1, words, 0, 0)], words, [(0, 5)])
    short = build_highlight_ass([_item(0, 4, words, 0, 0)], words, [(0, 5)])
    assert all(token in _events(short)[-1] for token in tokens[:5])
    assert _events(short)[-1].count(r"\N") <= 1
    assert HighlightStyle().tamanho == 72


def test_position_avoids_face_box_for_entire_highlight():
    words = _words(("Água", 0.5, 0.8, 0, 0), ("mudou.", 0.9, 1.3, 0, 0))
    item = _item(0, 1, words, 0, 0)
    centered = build_highlight_ass([item], words, [(0, 4)])
    shifted = build_highlight_ass(
        [item], words, [(0, 4)], caixas_ocupadas=lambda start, end: [(400, 150, 280, 250)]
    )
    assert r"\an8\pos(540," in _events(centered)[0]
    assert r"\an8\pos(540,1382)" in _events(shifted)[0]


def test_project_rejects_cuts_off_render_frame_grid(tmp_path):
    project = Project(
        timeline=Timeline(clipes=[Clip(arquivo=str(tmp_path / "fala.mp4"), trechos=[(0, 2.016)])])
    )
    project.timeline.recalcular_offsets()
    plano = PlanoImagens(assinatura=timeline_signature(project))
    with pytest.raises(ValueError, match="grade de quadros"):
        write_highlights_project(
            project, plano, lambda path: None, tmp_path / "destaque.ass", fps=30
        )

    snapped = Project(
        timeline=Timeline(
            clipes=[
                Clip(arquivo=str(tmp_path / "fala.mp4"), trechos=[(0, round(32 / 30, 6))])
            ]
        )
    )
    snapped.timeline.recalcular_offsets()
    valid_plan = PlanoImagens(assinatura=timeline_signature(snapped))
    subtitle = write_highlights_project(
        snapped,
        valid_plan,
        lambda path: SimpleNamespace(palavras=[]),
        tmp_path / "valido.ass",
        fps=30,
    )
    assert subtitle.is_file()


def _clip(path: Path, color: str, duration: float = 2.0) -> Path:
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    command += ["-f", "lavfi", "-i", f"color=c={color}:s=320x576:r=30:d={duration}"]
    command += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}"]
    command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    subprocess.run([*command, str(path)], check=True, capture_output=True)
    return path


def _frame(video: Path, second: float) -> np.ndarray:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            str(second),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(result.stdout, np.uint8).reshape(576, 320, 3)


@requires_ffmpeg
def test_burned_highlight_reveals_words_and_leaves_next_clip_clean(tmp_path):
    first = _clip(tmp_path / "first.mp4", "black", 3)
    second = _clip(tmp_path / "second.mp4", "0x000020", 3)
    project = Project(
        timeline=Timeline(
            clipes=[
                Clip(arquivo=str(first), trechos=[(0, 3)]),
                Clip(arquivo=str(second), trechos=[(0, 3)]),
            ]
        )
    )
    project.timeline.recalcular_offsets()
    words = _words(
        ("A", 0.5, 0.8, 0, 0),
        ("água", 0.9, 1.2, 0, 0),
        ("mudou.", 1.3, 1.7, 0, 0),
    )
    item = _item(0, 2, words, 0, 0)
    subtitle = write_highlight_ass(
        [item], words, [(0, 3), (3, 6)], tmp_path / "destaque.ass", saida=(320, 576)
    )
    video = render_timeline(
        project.timeline, tmp_path / "destaque.mp4", size=(320, 576), legendas=subtitle
    )

    def visible(second: float) -> int:
        frame = _frame(video, second)
        return int(((frame[..., 0] > 150) & (frame[..., 1] > 150)).sum())

    assert visible(0.2) == 0
    assert visible(0.6) > 10
    assert visible(1.1) > visible(0.6) * 2
    assert visible(1.55) > visible(1.1)
    assert visible(1.8) > visible(1.1)  # frase completa permanece após o fim da fala
    assert visible(3.15) == 0  # não vaza para o clipe seguinte


@requires_ffmpeg
def test_pipeline_switches_three_caption_modes_without_residual_text(tmp_path, monkeypatch):
    import src.pipeline as pipeline

    video = _clip(tmp_path / "fala.mp4", "black", 4)
    meta = ClipMeta(duracao=4, largura=320, altura=576, fps=30, tem_audio=True)
    project = Project(
        timeline=Timeline(clipes=[Clip(arquivo=str(video), trechos=[(0, 4)], meta=meta)])
    )
    project.timeline.recalcular_offsets()
    words = _words(
        ("Água", 0.5, 0.8, 0, 0),
        ("mudou", 0.9, 1.2, 0, 0),
        ("tudo.", 1.3, 1.7, 0, 0),
    )
    transcript = SimpleNamespace(palavras=[p.palavra for p in words])
    monkeypatch.setattr(pipeline, "transcribe_clip", lambda path: transcript)
    monkeypatch.setattr(pipeline, "face_tracks", lambda *args, **kwargs: {0: None})
    plan = PlanoImagens(
        assinatura=timeline_signature(project),
        destaques=[_item(0, 2, words, 0, 0)],
        versao_destaques=2,
        duracao_permanencia_destaques=1.2,
    )
    base = dict(cortes=False, cortes_fala=False, reenquadrar=False, imagens=False, zooms=False)
    output = tmp_path / "final.mp4"

    render_project(
        project,
        output,
        PipelineOptions(**base, legendas_continuas=False, legendas_destaque=True),
        plano=plan,
    )
    with_highlight = _frame(output, 1.1)
    assert int((with_highlight[:180, :, 0] > 150).sum()) > 10
    assert project.documento.por_id("highlight_000") is not None
    assert not [e for e in project.documento.elementos if e.tipo == "legenda"]

    render_project(
        project,
        output,
        PipelineOptions(**base, legendas_continuas=False, legendas_destaque=False),
        plano=plan,
    )
    without = _frame(output, 1.1)
    assert int((without[..., 0] > 150).sum()) == 0
    assert not [e for e in project.documento.elementos if e.tipo == "legenda"]

    render_project(
        project,
        output,
        PipelineOptions(**base, legendas_continuas=True, legendas_destaque=False),
        plano=plan,
    )
    continuous = _frame(output, 1.1)
    assert int((continuous[:180, :, 0] > 150).sum()) == 0
    assert int((continuous[180:, :, 0] > 150).sum()) > 10
    assert [e for e in project.documento.elementos if e.tipo == "legenda"]
