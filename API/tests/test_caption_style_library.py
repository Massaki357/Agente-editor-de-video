"""Etapa 3: biblioteca visual inicial do Caption Style Engine."""

from __future__ import annotations

from src.caption_presets import presets_disponiveis, resolve_preset
from src.captions import ARQUIVO_FONTE, FONTS_DIR, build_ass, group_words, text_width
from src.transcribe import Palavra

PRESETS = ("clean", "bold", "minimal", "cinematic", "social", "karaoke")


def _words() -> list[Palavra]:
    textos = ["Edição", "ágil", "transforma", "ideias", "em", "histórias."]
    return [
        Palavra(indice=i, texto=texto, inicio=i * 0.35, fim=(i + 0.8) * 0.35)
        for i, texto in enumerate(textos)
    ]


def test_initial_library_registers_all_six_generic_presets():
    assert set(PRESETS).issubset(presets_disponiveis())
    for name in PRESETS:
        preset = resolve_preset(name)
        assert preset.nome == name
        assert preset.descricao
        assert preset.animacao == "none"


def test_presets_cover_every_requested_visual_axis():
    styles = [resolve_preset(name).style for name in PRESETS]
    assert len({style.fonte for style in styles}) >= 3
    assert len({style.tamanho for style in styles}) >= 4
    assert {style.negrito for style in styles} == {False, True}
    assert len({style.contorno for style in styles}) >= 4
    assert len({style.sombra for style in styles}) >= 4
    assert any(style.fundo for style in styles)
    assert len({style.margem_inferior for style in styles}) >= 5
    assert {style.alinhamento for style in styles} == {"inferior", "centro", "superior"}
    assert len({style.cor_destaque for style in styles}) == len(PRESETS)
    assert len({style.palavras_max for style in styles}) >= 4


def test_every_font_is_bundled_and_declared():
    for name in PRESETS:
        font_name = resolve_preset(name).style.fonte
        assert font_name in ARQUIVO_FONTE
        assert (FONTS_DIR / ARQUIVO_FONTE[font_name]).is_file()


def test_presets_have_distinct_ass_styles_and_word_highlight():
    style_lines = set()
    for name in PRESETS:
        style = resolve_preset(name).style
        groups = [group_words(_words(), style)]
        ass = build_ass(groups, style)
        style_lines.add(next(line for line in ass.splitlines() if line.startswith("Style:")))
        dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        assert dialogues
        assert any("\\c&H" in line and "\\fscx" in line for line in dialogues)
    assert len(style_lines) == len(PRESETS)


def test_accents_and_unicode_are_preserved_by_every_preset():
    words = [
        Palavra(indice=0, texto="ação", inicio=0.0, fim=0.4),
        Palavra(indice=1, texto="coração", inicio=0.4, fim=0.9),
        Palavra(indice=2, texto="—", inicio=0.9, fim=1.1),
        Palavra(indice=3, texto="ótimo!", inicio=1.1, fim=1.6),
    ]
    for name in PRESETS:
        style = resolve_preset(name).style
        ass = build_ass([group_words(words, style)], style)
        expected = "AÇÃO" if style.maiusculas else "ação"
        assert expected in ass
        assert ("CORAÇÃO" if style.maiusculas else "coração") in ass
        assert ("ÓTIMO!" if style.maiusculas else "ótimo!") in ass


def test_grouping_fits_vertical_and_horizontal_safe_areas():
    for output in ((1080, 1920), (1920, 1080)):
        for name in PRESETS:
            style = resolve_preset(name).style.for_output(output)
            x, y, width, height = style.box(output)
            assert 0 <= x < output[0] and x + width <= output[0]
            assert 0 <= y < output[1] and y + height <= output[1]
            groups = group_words(_words(), style, (0.0, 3.0), output)
            assert groups and all(len(group.palavras) <= style.palavras_max for group in groups)
            safe_width = (output[0] - 2 * style.margem_lateral) / (style.destaque_escala / 100)
            for group in groups:
                text = " ".join(word.texto for word in group.palavras)
                assert text_width(text.upper() if style.maiusculas else text, style) <= safe_width


def test_default_remains_separate_from_the_new_library():
    default = resolve_preset("default").style
    assert all(resolve_preset(name).style != default for name in PRESETS)
