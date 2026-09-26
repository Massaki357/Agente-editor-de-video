"""Etapa 2 do PLANO_CAPTIONS_TRANSITIONS: fundação do Caption Style Engine.

O preset "default" precisa reproduzir exatamente o `CaptionStyle()` que já existia
antes desta etapa (nenhum vídeo existente pode mudar de aparência), e um nome
desconhecido precisa cair nele em vez de quebrar o render.
"""

import logging
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.caption_presets import (
    PRESET_PADRAO,
    CaptionPreset,
    presets_disponiveis,
    resolve_preset,
    resolved_style,
)
from src.captions import CaptionStyle, build_ass, group_words, write_captions
from src.clips import project_from_files
from src.pipeline import PipelineOptions
from src.render import render_timeline
from src.transcribe import Palavra


def frase(*itens) -> list[Palavra]:
    return [Palavra(indice=i, texto=t, inicio=a, fim=b) for i, (t, a, b) in enumerate(itens)]


# ------------------------------------------------------------------ registro/fallback


def test_default_preset_is_registered_and_matches_captionstyle_defaults():
    assert PRESET_PADRAO in presets_disponiveis()
    preset = resolve_preset(PRESET_PADRAO)
    assert isinstance(preset, CaptionPreset)
    assert preset.style == CaptionStyle()
    assert preset.animacao == "none"  # só "none" existe até a Etapa 4


def test_missing_preset_name_uses_default():
    assert resolve_preset(None).style == CaptionStyle()


def test_unknown_preset_falls_back_to_default_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="src.caption_presets"):
        preset = resolve_preset("estilo-que-nao-existe")
    assert preset.style == CaptionStyle()
    assert any("estilo-que-nao-existe" in r.message for r in caplog.records)


# ------------------------------------------------------------------ resolved_style


def test_resolved_style_uses_the_preset_when_no_override_is_given():
    assert resolved_style("default", None) == CaptionStyle()
    assert resolved_style(None, None) == CaptionStyle()


def test_resolved_style_prefers_an_explicit_override():
    explicito = CaptionStyle(cor="#00FF00")
    assert resolved_style("default", explicito) is explicito


def test_resolved_style_falls_back_even_with_an_override_absent():
    # nome desconhecido + sem override: continua caindo no default, não quebra
    assert resolved_style("nao-existe", None) == CaptionStyle()


# ------------------------------------------------------------------ configuração visual nova

S = CaptionStyle()


def test_default_style_keeps_the_historical_ass_encoding():
    """Trava os valores que os campos novos (negrito/fundo/alinhamento) substituíram."""
    grupos = [group_words(frase(("oi", 0.0, 0.4)), S)]
    ass = build_ass(grupos, S)
    estilo = next(linha for linha in ass.splitlines() if linha.startswith("Style: Legenda"))
    campos = estilo.split(",")
    assert campos[7] == "-1"  # Bold: negrito=True (padrão)
    assert campos[15] == "1"  # BorderStyle: fundo=False (só contorno, sem caixa)
    assert campos[18] == "2"  # Alignment: alinhamento="inferior"


def test_negrito_false_disables_the_bold_flag():
    regular = CaptionStyle(negrito=False)
    ass = build_ass([group_words(frase(("oi", 0.0, 0.4)), regular)], regular)
    estilo = next(linha for linha in ass.splitlines() if linha.startswith("Style: Legenda"))
    assert estilo.split(",")[7] == "0"


def test_fundo_true_switches_to_the_opaque_box_border_style():
    com_fundo = CaptionStyle(fundo=True)
    ass = build_ass([group_words(frase(("oi", 0.0, 0.4)), com_fundo)], com_fundo)
    estilo = next(linha for linha in ass.splitlines() if linha.startswith("Style: Legenda"))
    assert estilo.split(",")[15] == "3"


@pytest.mark.parametrize(
    ("alinhamento", "esperado"),
    [
        ("inferior", "2"),
        ("centro", "5"),
        ("superior", "8"),
    ],
)
def test_alinhamento_maps_to_the_ass_numpad_value(alinhamento, esperado):
    estilo_ = CaptionStyle(alinhamento=alinhamento)
    ass = build_ass([group_words(frase(("oi", 0.0, 0.4)), estilo_)], estilo_)
    linha = next(linha for linha in ass.splitlines() if linha.startswith("Style: Legenda"))
    assert linha.split(",")[18] == esperado


@pytest.mark.parametrize("alinhamento", ["inferior", "centro", "superior"])
def test_box_stays_inside_the_frame_for_every_alignment(alinhamento):
    x, y, w, h = CaptionStyle(alinhamento=alinhamento).box()
    assert 0 <= x and x + w <= 1080
    assert 0 <= y and y + h <= 1920


def test_box_for_the_default_alignment_is_unchanged():
    # mesma fórmula de antes desta etapa, para o preset "default" continuar idêntico
    x, y, w, h = S.box()
    folga = int(S.contorno + S.sombra) + 12
    alt_linha = int(S.tamanho * 1.4 * S.destaque_escala / 100)
    y_base = 1920 - S.margem_inferior
    y_esperado = max(0, y_base - alt_linha - folga - int(0.15 * S.tamanho))
    assert (x, y) == (max(0, S.margem_lateral - folga), y_esperado)
    assert h == min(y_base + folga - y_esperado, 1920 - y_esperado)


# ------------------------------------------------------------------ PipelineOptions


def test_pipeline_options_default_to_the_default_preset_without_an_override():
    options = PipelineOptions()
    assert options.preset_legenda == PRESET_PADRAO
    assert options.estilo_legenda is None
    assert resolved_style(options.preset_legenda, options.estilo_legenda) == CaptionStyle()


def test_pipeline_options_still_accept_a_partial_style_override():
    """Compatibilidade com o uso já testado em test_api.py (dict parcial de CaptionStyle)."""
    options = PipelineOptions(estilo_legenda={"cor_destaque": "#00FF00", "maiusculas": False})
    assert options.estilo_legenda == CaptionStyle(cor_destaque="#00FF00", maiusculas=False)


# ------------------------------------------------------------------ vídeo de regressão


def _video(path: Path, dur: float = 2.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
        + ["-f", "lavfi", "-i", f"color=c=black:s=1080x1920:r=30:d={dur}"]
        + ["-f", "lavfi", "-i", f"sine=frequency=300:sample_rate=48000:duration={dur}"]
        + ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"]
        + ["-shortest", str(path)],
        check=True,
        capture_output=True,
    )
    return path


def _frame(video: Path, t: float) -> np.ndarray:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", str(t)]
        + ["-i", str(video), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True,
        capture_output=True,
    )
    return np.frombuffer(proc.stdout, np.uint8).reshape(1920, 1080, 3).astype(int)


def test_regression_video_with_the_legacy_preset_still_burns_readable_captions(tmp_path):
    """Vídeo de regressão da Etapa 2: o preset default, resolvido pelo registry, precisa
    queimar a legenda exatamente como o `CaptionStyle()` direto queimava antes desta etapa
    (mesma asserção de `test_burned_captions_are_readable_on_dark_and_light_backgrounds`)."""
    clip = _video(tmp_path / "fundo.mp4")
    project = project_from_files([clip])
    palavras = frase(("legenda", 0.2, 0.9), ("visivel.", 0.9, 1.4))
    estilo = resolve_preset(PRESET_PADRAO).style
    ass = write_captions([(palavras, (0.0, 2.0))], tmp_path / "leg.ass", estilo)
    out = render_timeline(project.timeline, tmp_path / "out.mp4", legendas=ass)

    x, y, w, h = estilo.box()
    area = _frame(out, 0.5)[y : y + h, x : x + w]
    brancos = np.all(area > 220, axis=2).sum()
    amarelos = ((area[..., 0] > 200) & (area[..., 1] > 170) & (area[..., 2] < 80)).sum()
    assert amarelos > 500  # a palavra atual ("LEGENDA") em destaque
    assert brancos > 500  # texto branco sobre fundo preto
