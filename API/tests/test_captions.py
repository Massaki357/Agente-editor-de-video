"""Etapa 7: legendas .ass palavra por palavra, queimadas na passada 2."""

import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.captions import (
    FONTS_DIR,
    CaptionStyle,
    build_ass,
    group_words,
    text_width,
    texto_exibido,
    write_captions,
)
from src.clips import project_from_files
from src.cuts import TimeMap
from src.project import Clip, Timeline
from src.render import render_timeline
from src.transcribe import Palavra

S = CaptionStyle()


def frase(*itens) -> list[Palavra]:
    return [Palavra(indice=i, texto=t, inicio=a, fim=b) for i, (t, a, b) in enumerate(itens)]


# ------------------------------------------------------------------ texto e grupos


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [("mudar...", "MUDAR"), ("gente,", "GENTE"), ("né?", "NÉ?"), ("(ok)", "OK"), ("ah!", "AH!")],
)
def test_display_text_strips_punctuation_but_keeps_questions(bruto, esperado):
    assert texto_exibido(bruto, maiusculas=True) == esperado


def test_groups_have_at_most_4_words_and_close_at_sentence_end():
    palavras = frase(
        ("hoje", 0.0, 0.2), ("eu", 0.2, 0.3), ("vou", 0.3, 0.5), ("mostrar", 0.5, 0.9),
        ("como", 0.9, 1.1), ("fazer.", 1.1, 1.5), ("Primeiro,", 1.6, 2.0), ("pegue", 2.0, 2.3),
    )  # fmt: skip
    pequena = CaptionStyle(tamanho=50)  # fonte menor: aqui só a regra de 4 palavras limita
    grupos = group_words(palavras, pequena)
    textos = [[w.texto for w in g.palavras] for g in grupos]
    assert textos == [["hoje", "eu", "vou", "mostrar"], ["como", "fazer."], ["Primeiro,", "pegue"]]
    # na fonte padrão (84 px), "HOJE EU VOU MOSTRAR" não cabe: a largura fecha antes
    assert [w.texto for w in group_words(palavras, S)[0].palavras] == ["hoje", "eu", "vou"]
    assert all(len(g.palavras) <= 4 for g in grupos)


def test_single_word_sentence_is_not_glued_to_the_next():
    grupos = group_words(frase(("Não.", 0.0, 0.3), ("mês", 0.35, 0.6), ("que", 0.6, 0.7)), S)
    assert [[w.texto for w in g.palavras] for g in grupos] == [["Não."], ["mês", "que"]]


def test_long_pause_starts_a_new_group():
    grupos = group_words(frase(("a", 0.0, 0.2), ("b", 1.0, 1.2)), S)
    assert len(grupos) == 2
    assert grupos[0].fim <= grupos[1].inicio


def test_group_never_wider_than_the_safe_area():
    longas = frase(*[(p, i * 0.3, i * 0.3 + 0.25) for i, p in enumerate(
        ["extraordinariamente", "inconstitucionalissimamente", "paralelepípedo", "otorrino"]
    )])  # fmt: skip
    largura_max = (1080 - 2 * S.margem_lateral) / (S.destaque_escala / 100)
    for g in group_words(longas, S):
        texto = " ".join(texto_exibido(w.texto, True) for w in g.palavras)
        assert text_width(texto, S) <= largura_max or len(g.palavras) == 1


def test_text_width_uses_the_real_font():
    assert (FONTS_DIR / "Poppins-Bold.ttf").exists()
    assert text_width("WWWW", S) > text_width("IIII", S) * 2  # fonte proporcional de verdade


def test_group_stays_on_screen_a_little_after_the_last_word():
    (g,) = group_words(frase(("oi", 1.0, 1.3)), S)
    assert (g.inicio, g.fim) == (1.0, pytest.approx(1.7))


# ------------------------------------------------------------------ emendas


def test_no_caption_crosses_a_clip_seam():
    timeline = Timeline(
        clipes=[
            Clip(arquivo="a.mp4", trechos=[(0.0, 2.0)]),
            Clip(arquivo="b.mp4", trechos=[(0.5, 2.5)]),
        ]
    )
    timeline.recalcular_offsets()
    tm = TimeMap(timeline)
    # última palavra do clipe a termina colada no fim; a do b começa colada no início
    a = tm.words_to_out(0, frase(("fim", 1.5, 1.98)))
    b = tm.words_to_out(1, frase(("começo", 0.52, 0.9), ("aqui", 0.9, 1.2)))
    grupos_a = group_words(a, S, tm.clip_bounds(0))
    grupos_b = group_words(b, S, tm.clip_bounds(1))
    emenda = tm.seams()[0]
    assert emenda == pytest.approx(2.0)
    assert all(g.fim <= emenda + 1e-9 for g in grupos_a)  # o "hold" de 0,4 s foi cortado
    assert all(g.inicio >= emenda - 1e-9 for g in grupos_b)


# ------------------------------------------------------------------ arquivo .ass


def test_ass_file_structure_and_one_highlight_per_event():
    grupos = [group_words(frase(("olá", 0.0, 0.4), ("mundo.", 0.4, 0.9)), S)]
    ass = build_ass(grupos, S)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    estilo = next(linha for linha in ass.splitlines() if linha.startswith("Style: Legenda"))
    campos = estilo.split(",")
    assert campos[1] == "Poppins" and campos[2] == str(round(84 / 0.5675))  # em 84 px
    assert campos[3] == "&H00FFFFFF" and campos[5] == "&H00000000"  # branco, contorno preto
    assert campos[18] == "2" and campos[21] == "520"  # centro-baixo, acima da UI das redes
    eventos = [linha for linha in ass.splitlines() if linha.startswith("Dialogue:")]
    assert len(eventos) == 2
    for ev in eventos:
        assert ev.count("\\c&H00D4FF&") == 1  # só a palavra atual em amarelo
    assert "OLÁ" in eventos[0] and "MUNDO" in eventos[1]
    tempos = [re.findall(r"\d:\d\d:\d\d\.\d\d", ev) for ev in eventos]
    assert tempos == [["0:00:00.00", "0:00:00.40"], ["0:00:00.40", "0:00:01.30"]]


def test_braces_in_text_cannot_inject_ass_tags():
    grupos = [group_words(frase(("{\\b1}oi", 0.0, 0.5)), S)]
    ass = build_ass(grupos, S)
    assert "{\\b1}" not in ass


def test_caption_box_is_inside_the_frame_and_above_the_bottom_ui():
    x, y, w, h = S.box()
    assert 0 <= x and x + w <= 1080 and 0 <= y and y + h <= 1920
    assert y + h <= 1920 - S.margem_inferior + 40  # a área de botões fica livre
    assert y > 1920 / 2  # terço inferior


def test_style_scales_with_output_height():
    menor = S.for_output((720, 1280))
    assert menor.tamanho == round(84 * 1280 / 1920)
    assert menor.margem_inferior == round(520 * 1280 / 1920)
    assert S.for_output((1080, 1920)) is S


# ------------------------------------------------------------------ queimado no vídeo


def _video(path: Path, cor: str, dur: float = 2.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
        + ["-f", "lavfi", "-i", f"color=c={cor}:s=1080x1920:r=30:d={dur}"]
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


@pytest.mark.parametrize("fundo", ["black", "white"])
def test_burned_captions_are_readable_on_dark_and_light_backgrounds(tmp_path, fundo):
    clip = _video(tmp_path / "fundo.mp4", fundo)
    project = project_from_files([clip])
    palavras = frase(("legenda", 0.2, 0.9), ("visível.", 0.9, 1.4))
    ass = write_captions([(palavras, (0.0, 2.0))], tmp_path / "leg.ass")
    out = render_timeline(project.timeline, tmp_path / "out.mp4", legendas=ass)

    x, y, w, h = S.box()
    area = _frame(out, 0.5)[y : y + h, x : x + w]
    brancos = np.all(area > 220, axis=2).sum()
    escuros = np.all(area < 40, axis=2).sum()
    amarelos = ((area[..., 0] > 200) & (area[..., 1] > 170) & (area[..., 2] < 80)).sum()
    assert amarelos > 500  # a palavra atual ("LEGENDA") em destaque
    # contorno preto no fundo branco / texto branco no fundo preto: sempre contraste
    assert brancos > 500 and escuros > 500

    fora = _frame(out, 1.95)[y : y + h, x : x + w]  # depois do fim da legenda
    assert np.ptp(fora.mean(axis=2)) < 30  # só o fundo


def test_libass_loads_the_bundled_font(tmp_path):
    """Sem a fonte do projeto, o libass trocaria em silêncio por outra."""
    clip = _video(tmp_path / "v.mp4", "gray", dur=0.5)
    ass = write_captions([(frase(("teste", 0.0, 0.4)), (0.0, 0.5))], tmp_path / "leg.ass")
    (tmp_path / "fonts").mkdir()
    for fonte in FONTS_DIR.glob("*.ttf"):
        (tmp_path / "fonts" / fonte.name).write_bytes(fonte.read_bytes())
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-v", "verbose", "-i", clip.name]
        + ["-vf", f"ass={ass.name}:fontsdir=fonts", "-frames:v", "1", "-f", "null", "-"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    linhas = [linha for linha in proc.stderr.splitlines() if "fontselect" in linha]
    assert linhas and all("Poppins" in linha for linha in linhas), linhas


# ------------------------------------------------------------------ regressões do verificador


def test_caption_does_not_bleed_one_frame_into_the_next_clip(tmp_path):
    """Emenda em 62/30 = 2,0667 s: o fim arredondado para cima (2,07) mostrava a legenda
    do clipe A no 1º frame do clipe B."""
    a = _video(tmp_path / "a.mp4", "black", dur=3.0)
    b = _video(tmp_path / "b.mp4", "black", dur=3.0)
    project = project_from_files([a, b])
    project.timeline.substituir_trechos(0, [(0.0, 62 / 30)])
    project.timeline.substituir_trechos(1, [(0.0, 1.0)])
    tm = TimeMap(project.timeline)
    assert tm.seams()[0] == pytest.approx(62 / 30)
    ass = write_captions(
        [
            (tm.words_to_out(0, frase(("fim", 1.5, 2.06))), tm.clip_bounds(0)),
            (tm.words_to_out(1, frase(("depois", 0.3, 0.8))), tm.clip_bounds(1)),
        ],
        tmp_path / "leg.ass",
    )
    out = render_timeline(project.timeline, tmp_path / "out.mp4", legendas=ass)
    x, y, w, h = S.box()
    # frame 62 (1º do clipe B): nada ainda ("DEPOIS" só entra em 2,0667 + 0,3)
    # _frame(t) devolve o 1º frame com tempo >= t: k/30 - 0,001 pega o frame k
    quadro = _frame(out, 62 / 30 - 0.001)[y : y + h, x : x + w]
    assert np.all(quadro.mean(axis=2) < 30), "a legenda do clipe A vazou para o clipe B"
    assert np.any(_frame(out, 61 / 30 - 0.001)[y : y + h, x : x + w] > 200)  # frame 61: "FIM"


def test_ass_times_never_round_up():
    grupos = [group_words(frase(("fim", 1.5, 2.0)), S, (0.0, 62 / 30))]
    evento = next(linha for linha in build_ass(grupos, S).splitlines() if linha.startswith("Dia"))
    assert ",0:00:02.06," in evento  # 2,0667 → 2,06 (nunca 2,07)


def test_backslash_cannot_become_an_ass_command():
    ass = build_ass([group_words(frase((r"ab\Ncd", 0.0, 0.5)), S)], S)
    evento = next(linha for linha in ass.splitlines() if linha.startswith("Dialogue"))
    assert r"\N" not in evento and "AB/NCD" in evento


def test_single_very_long_word_is_scaled_to_fit():
    ass = build_ass([group_words(frase(("extraordinariamente", 0.0, 1.0)), S)], S)
    evento = next(linha for linha in ass.splitlines() if linha.startswith("Dialogue"))
    base = int(re.search(r"^Dialogue:[^{]*\{\\fscx(\d+)", evento).group(1))
    largura = text_width("EXTRAORDINARIAMENTE", S) * base / 100 * S.destaque_escala / 100
    assert base < 100 and largura <= 1080 - 2 * S.margem_lateral + 1


def test_unknown_font_is_rejected():
    with pytest.raises(ValueError, match="indisponível"):
        CaptionStyle(fonte="Comic Sans")


def test_residue_of_a_cut_word_does_not_flash_in_the_caption():
    from src.pipeline import visible_words

    timeline = Timeline(clipes=[Clip(arquivo="a.mp4", trechos=[(0.0, 1.0), (1.5, 3.0)])])
    timeline.recalcular_offsets()
    tm = TimeMap(timeline)
    palavras = frase(("fica", 0.2, 0.6), ("cortada", 0.98, 1.5), ("volta", 1.6, 2.0))
    assert [p.texto for p in visible_words(tm, 0, palavras)] == ["fica", "volta"]


def test_early_whisper_start_over_a_cut_pause_keeps_the_word():
    """Regressão: "A" (55,64–55,98) com o trecho começando em 55,9 sumia da legenda."""
    from src.pipeline import visible_words

    timeline = Timeline(clipes=[Clip(arquivo="a.mp4", trechos=[(0.0, 1.0), (1.9, 3.0)])])
    timeline.recalcular_offsets()
    tm = TimeMap(timeline)
    palavras = frase(("fila.", 0.5, 0.95), ("A", 1.64, 1.98), ("gente", 1.98, 2.3))
    assert [p.texto for p in visible_words(tm, 0, palavras)] == ["fila.", "A", "gente"]


def test_measured_width_matches_what_libass_draws(tmp_path):
    """A largura usada para agrupar (Pillow) tem que bater com o desenho do libass."""
    from src.captions import Grupo

    clip = _video(tmp_path / "bg.mp4", "black", dur=0.5)
    (tmp_path / "fonts").mkdir()
    for fonte in FONTS_DIR.glob("*.ttf"):
        (tmp_path / "fonts" / fonte.name).write_bytes(fonte.read_bytes())
    estilo = CaptionStyle(contorno=0, sombra=0, destaque_escala=100)
    # frases que cabem na área segura (as maiores são reduzidas de propósito)
    for texto in ("EU VOU MOSTRAR", "JÁ ADIOU EXAMES?", "WWWWW"):
        grupo = Grupo([Palavra(indice=0, texto=texto, inicio=0, fim=1)], 0, 1)
        (tmp_path / "t.ass").write_text(build_ass([[grupo]], estilo), encoding="utf-8")
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", clip.name]
            + ["-vf", "ass=t.ass:fontsdir=fonts", "-frames:v", "1"]
            + ["-f", "rawvideo", "-pix_fmt", "gray", "-"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
        img = np.frombuffer(proc.stdout, np.uint8).reshape(1920, 1080)
        colunas = np.flatnonzero(img.max(axis=0) > 128)
        desenhada = colunas.max() - colunas.min() + 1
        # ~10 px de diferença fixa: bordas anti-aliased abaixo do limiar de 128
        assert desenhada == pytest.approx(text_width(texto, estilo), rel=0.05), texto


def test_short_whole_word_is_kept_even_if_whisper_gave_it_20ms():
    """Regressão: "você" (20 ms no Whisper) e "quê" (40 ms), inteiros no trecho, sumiam."""
    from src.pipeline import visible_words

    timeline = Timeline(clipes=[Clip(arquivo="a.mp4", trechos=[(0.0, 3.0)])])
    timeline.recalcular_offsets()
    tm = TimeMap(timeline)
    palavras = frase(("você", 1.62, 1.64), ("fala", 1.64, 1.9), ("quê", 2.04, 2.08))
    assert [p.texto for p in visible_words(tm, 0, palavras)] == ["você", "fala", "quê"]


def test_box_top_covers_accented_capitals():
    x, y, w, h = S.box()
    base = 1920 - S.margem_inferior
    # topo de um "Í" a 112%: ~1,05 em acima da base, mais o contorno
    assert y <= base - 1.05 * S.tamanho * S.destaque_escala / 100 - S.contorno
