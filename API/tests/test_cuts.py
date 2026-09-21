import subprocess
from pathlib import Path

import pytest

from src.clips import project_from_files
from src.config import Settings, get_settings
from src.cuts import (
    CutParams,
    TimeMap,
    apply_cuts,
    complement,
    detect_silences,
    keep_segments,
    parse_silencedetect,
    refine_with_silences,
    speech_from_words,
)
from src.project import Clip, Timeline
from src.transcribe import Palavra, Transcricao

P = CutParams()
FRAME = 1 / 30


def w(i, texto, a, b) -> Palavra:
    return Palavra(indice=i, texto=texto, inicio=a, fim=b)


# ------------------------------------------------------------------ funções puras


def test_parse_silencedetect_with_open_ending():
    log = (
        "[silencedetect @ 0x1] silence_start: -0.002\n"
        "[silencedetect @ 0x1] silence_end: 0.95 | silence_duration: 0.95\n"
        "[silencedetect @ 0x1] silence_start: 2.5\n"
        "[silencedetect @ 0x1] silence_end: 3.1 | silence_duration: 0.6\n"
        "[silencedetect @ 0x1] silence_start: 5.2\n"
    )
    assert parse_silencedetect(log, 6.0) == [(0.0, 0.95), (2.5, 3.1), (5.2, 6.0)]


def test_words_close_together_form_one_block():
    palavras = [w(0, "a", 1.0, 1.3), w(1, "b", 1.5, 2.0), w(2, "c", 2.6, 3.0)]
    assert speech_from_words(palavras, 0.4) == [(1.0, 2.0), (2.6, 3.0)]


def test_refine_moves_early_start_to_real_speech():
    # Whisper diz que a fala começa em 9.80, mas o silêncio vai até 10.23.
    assert refine_with_silences([(9.80, 11.0)], [(9.27, 10.23)], 0.4) == [(10.23, 11.0)]


def test_refine_moves_late_end_and_splits_inner_pause():
    fala = [(1.0, 6.0)]
    silences = [(2.0, 2.6), (5.5, 7.0), (3.0, 3.2)]  # 3.0-3.2 é curto demais para dividir
    assert refine_with_silences(fala, silences, 0.4) == [(1.0, 2.0), (2.6, 5.5)]


def test_refine_drops_block_fully_inside_silence():
    assert refine_with_silences([(2.0, 2.5)], [(1.5, 3.0)], 0.4) == []


def test_complement():
    assert complement([(0.0, 1.0), (2.0, 3.0)], 4.0) == [(1.0, 2.0), (3.0, 4.0)]
    assert complement([], 2.0) == [(0.0, 2.0)]


def test_keep_segments_adds_margin_and_snaps_to_frame_grid():
    palavras = [w(0, "oi", 1.01, 1.5), w(1, "tchau", 3.0, 3.52)]
    trechos = keep_segments(palavras, [], 10.0, P)
    assert trechos == [(pytest.approx(0.9), pytest.approx(1.6)), (2.9, pytest.approx(3.6))]
    for a, b in trechos:
        assert a * 30 == pytest.approx(round(a * 30))
        assert b * 30 == pytest.approx(round(b * 30))
        # o respiro nunca é menor que o pedido
    assert trechos[0][0] <= 1.01 - P.margem and trechos[0][1] >= 1.5 + P.margem


def test_keep_segments_trims_start_and_end_of_clip():
    # 2 s de silêncio no começo e 3 s no fim do clipe: a emenda não pode ter pausa.
    trechos = keep_segments([w(0, "fala", 2.0, 4.0)], [(0, 2.0), (4.0, 7.0)], 7.0, P)
    assert trechos == [(pytest.approx(1.9), pytest.approx(4.1))]


def test_keep_segments_clamps_to_clip_and_keeps_close_words_together():
    palavras = [w(0, "a", 0.02, 0.5), w(1, "b", 0.6, 1.99)]
    assert keep_segments(palavras, [], 2.0, P) == [(0.0, 2.0)]


def test_keep_segments_without_words_keeps_non_silence():
    trechos = keep_segments([], [(0.0, 1.0)], 3.0, P)
    assert trechos == [(pytest.approx(0.9), 3.0)]


def test_keep_segments_drops_tiny_segments():
    assert keep_segments([w(0, "x", 1.0, 1.0)], [], 5.0, CutParams(margem=0.0)) == []


# ------------------------------------------------------------------ remap de tempo


def _timeline() -> Timeline:
    t = Timeline(
        clipes=[
            Clip(arquivo="1.mp4", trechos=[(1.0, 3.0), (5.0, 6.0)]),  # 3 s mantidos
            Clip(arquivo="2.mp4", trechos=[(0.5, 2.5)]),  # 2 s
            Clip(arquivo="3.mp4", trechos=[(2.0, 3.0), (4.0, 4.5), (7.0, 8.0)]),  # 2,5 s
        ]
    )
    t.recalcular_offsets()
    return t


@pytest.mark.parametrize(
    ("clip", "t_src", "esperado"),
    [
        (0, 1.0, 0.0),
        (0, 2.5, 1.5),
        (0, 3.0, 2.0),  # fim do 1º trecho
        (0, 5.0, 2.0),  # começo do 2º trecho = mesmo instante de saída
        (0, 5.5, 2.5),
        (1, 0.5, 3.0),  # offset do 2º clipe
        (1, 2.0, 4.5),
        (2, 2.0, 5.0),
        (2, 4.25, 6.25),
        (2, 7.5, 7.0),
        (2, 8.0, 7.5),
    ],
)
def test_to_out_across_segments_and_clips(clip, t_src, esperado):
    assert TimeMap(_timeline()).to_out(clip, t_src) == pytest.approx(esperado)


def test_to_out_in_cut_region():
    tm = TimeMap(_timeline())
    assert tm.to_out(0, 4.0) is None
    assert tm.to_out(0, 4.0, snap="next") == pytest.approx(2.0)
    assert tm.to_out(0, 4.0, snap="prev") == pytest.approx(2.0)
    assert tm.to_out(2, 5.0, snap="next") == pytest.approx(6.5)
    assert tm.to_out(2, 5.0, snap="prev") == pytest.approx(6.5)
    assert tm.to_out(0, 0.5, snap="prev") is None  # antes do 1º trecho
    assert tm.to_out(0, 9.0, snap="next") is None  # depois do último trecho do clipe


@pytest.mark.parametrize("t_out", [0.0, 1.3, 2.0, 2.7, 3.0, 4.9, 5.0, 6.2, 6.9, 7.5])
def test_to_src_is_inverse_of_to_out(t_out):
    tm = TimeMap(_timeline())
    clip, t_src = tm.to_src(t_out)
    assert tm.to_out(clip, t_src) == pytest.approx(t_out)


def test_to_src_out_of_range():
    with pytest.raises(ValueError):
        TimeMap(_timeline()).to_src(7.6)


def test_duration_bounds_and_seams():
    tm = TimeMap(_timeline())
    assert tm.duracao == pytest.approx(7.5)
    assert tm.clip_bounds(1) == (pytest.approx(3.0), pytest.approx(5.0))
    assert tm.seams() == [pytest.approx(3.0), pytest.approx(5.0)]


def test_clip_without_segments_disappears_from_map():
    t = Timeline(clipes=[Clip(arquivo="a", trechos=[(0, 1)]), Clip(arquivo="b", trechos=[])])
    t.adicionar_clipe(Clip(arquivo="c", trechos=[(2, 3)]))
    tm = TimeMap(t)
    assert tm.clip_bounds(1) is None
    assert tm.seams() == [pytest.approx(1.0)]
    assert tm.to_out(2, 2.5) == pytest.approx(1.5)


def test_words_to_out_drops_cut_words_and_clamps_partial():
    tm = TimeMap(_timeline())
    palavras = [
        w(0, "mantida", 1.2, 1.6),
        w(1, "cortada", 3.5, 4.5),
        w(2, "parcial", 5.8, 6.4),  # termina depois do fim do trecho (6.0)
    ]
    out = tm.words_to_out(0, palavras)
    assert [(p.texto, p.inicio, p.fim) for p in out] == [
        ("mantida", pytest.approx(0.2), pytest.approx(0.6)),
        ("parcial", pytest.approx(2.8), pytest.approx(3.0)),
    ]


# ------------------------------------------------------------------ com FFmpeg real


def _tone_clip(path: Path) -> Path:
    """6 s: tom em 1–2 s e 3,5–4,5 s, silêncio no resto."""
    expr = "0.5*sin(2*PI*440*t)*(between(t,1,2)+between(t,3.5,4.5))"
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    cmd += ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=6"]
    cmd += ["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d=6"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    subprocess.run([*cmd, "-shortest", str(path)], check=True, capture_output=True)
    return path


@pytest.fixture(scope="module")
def tone_clip(tmp_path_factory) -> Path:
    return _tone_clip(tmp_path_factory.mktemp("cuts") / "tom.mp4")


def test_detect_silences_on_real_audio(tone_clip):
    silences = detect_silences(tone_clip, P)
    assert len(silences) == 3
    esperado = [(0.0, 1.0), (2.0, 3.5), (4.5, 6.0)]
    for (s, e), (es, ee) in zip(silences, esperado, strict=True):
        assert s == pytest.approx(es, abs=0.06)
        assert e == pytest.approx(ee, abs=0.06)


def test_apply_cuts_uses_silences_to_fix_whisper_early_start(tone_clip):
    palavras = [
        w(0, "um", 1.0, 1.4),
        w(1, "dois", 1.5, 2.0),
        w(2, "tres", 3.1, 3.9),  # Whisper antecipou: o som só volta em 3.5
        w(3, "quatro", 3.9, 4.5),
    ]

    def fake_transcriber(path, settings=None):
        return Transcricao(arquivo_hash="x", modelo="fake", palavras=palavras)

    project = project_from_files([tone_clip, tone_clip])
    apply_cuts(project, P, transcriber=fake_transcriber)

    for clip in project.timeline.clipes:
        (a1, b1), (a2, b2) = clip.trechos
        assert a1 == pytest.approx(0.9, abs=FRAME + 0.06)
        assert b1 == pytest.approx(2.1, abs=FRAME + 0.06)
        assert a2 == pytest.approx(3.4, abs=FRAME + 0.06)  # e não 3.0
        assert b2 == pytest.approx(4.6, abs=FRAME + 0.06)

    # a emenda entre os clipes não tem a pausa do fim do 1º nem a do começo do 2º
    c1, c2 = project.timeline.clipes
    assert c2.offset == pytest.approx(c1.duracao_mantida)
    assert c1.duracao_mantida == pytest.approx(2.4, abs=4 * FRAME)


def test_apply_cuts_on_clip_without_audio_keeps_it_whole(tmp_path):
    from tests.conftest import make_video

    silent = make_video(tmp_path / "mudo.mp4", duration=1.0, audio=False)

    def must_not_transcribe(path, settings=None):
        raise AssertionError("clipe sem áudio não deve ser transcrito")

    project = project_from_files([silent])
    apply_cuts(project, P, transcriber=must_not_transcribe)
    (a, b) = project.timeline.clipes[0].trechos[0]
    assert a == 0.0 and b == pytest.approx(1.0, abs=FRAME)


def test_silence_cache_respects_settings_dir(tone_clip, tmp_path):
    settings = Settings(cache_dir=tmp_path / "c")
    detect_silences(tone_clip, P, settings)
    assert len(list((tmp_path / "c" / "silencio").glob("*.json"))) == 1
    assert not (get_settings().cache_dir / "silencio").exists()


# ------------------------------------------------- bordas estendidas até o silêncio real


def test_end_is_extended_to_real_silence_start():
    # Whisper diz que a palavra acaba em 2.0, mas há som até 2.3 (o silêncio começa ali).
    trechos = keep_segments([w(0, "a", 1.0, 2.0)], [(0, 1.2), (2.3, 5)], 5.0, P)
    assert trechos == [(pytest.approx(1.1), pytest.approx(2.4))]  # 2.3 + margem, na grade


def test_start_is_extended_back_to_real_silence_end():
    # Whisper diz que a fala começa em 1.5, mas o som começa em 1.2.
    assert refine_with_silences([(1.5, 3.0)], [(0.0, 1.2), (3.0, 5.0)], 0.4) == [(1.2, 3.0)]


def test_extension_only_when_real_silence_is_in_reach():
    # Silêncio a 0,4 s: estende até ele. Silêncio a 1,5 s: é ruído, não estende.
    fala = refine_with_silences([(2.0, 3.0)], [(0.0, 1.6), (4.5, 6.0)], 0.4, extensao_max=0.5)
    assert fala == [(1.6, 3.0)]


def test_no_extension_without_silence_information():
    assert refine_with_silences([(2.0, 3.0)], [], 0.4) == [(2.0, 3.0)]


def test_timemap_matches_render_plan():
    from src.project import ClipMeta
    from src.render import plan_segments

    timeline = _timeline()
    metas = [ClipMeta(duracao=10, largura=320, altura=180, fps=30, tem_audio=True)] * 3
    plano = plan_segments(timeline, metas, 30)
    mapa = TimeMap(timeline).segments
    assert len(plano) == len(mapa)
    for seg, m in zip(plano, mapa, strict=True):
        assert seg.t_out == pytest.approx(m.out_inicio, abs=1e-5)
        assert seg.frames / 30 == pytest.approx(m.duracao, abs=1e-5)


@pytest.mark.parametrize("pausa", [0.5, 1.0, 2.0, 3.0])
def test_noisy_pause_whisper_saw_is_cut_down_to_margins(pausa):
    # Pausa com ruído de fundo: o silencedetect só vê silêncio longe dela.
    palavras = [w(0, "a", 1.0, 2.0), w(1, "b", 2.0 + pausa, 3.0 + pausa)]
    silences = [(0.0, 0.9), (3.5 + pausa, 9.0)]
    trechos = keep_segments(palavras, silences, 9.0, P)
    assert len(trechos) == 2
    pausa_mantida = (trechos[0][1] - 2.0) + (2.0 + pausa - trechos[1][0])
    assert pausa_mantida <= 2 * P.margem + 2 * FRAME


def test_silences_shorter_than_border_threshold_are_ignored():
    # Silêncio de 0,1 s dentro do fim da palavra (ex.: fechamento de um "t"): o som volta
    # e só termina em 2.3 — o fim do trecho vai até lá.
    silences = [(0, 0.9), (1.95, 2.05), (2.3, 5)]
    trechos = keep_segments([w(0, "a", 1.0, 2.0)], silences, 5.0, P)
    assert trechos == [(pytest.approx(0.8), pytest.approx(2.4))]


def test_short_silences_only_refine_borders_and_do_not_split():
    # 0,25 s de silêncio no meio da fala (curto demais para virar corte)
    fala = refine_with_silences([(1.0, 3.0)], [(1.9, 2.15)], 0.4)
    assert fala == [(1.0, 3.0)]


def test_without_words_only_long_silences_become_cuts():
    trechos = keep_segments([], [(1.0, 1.25), (2.0, 3.0)], 4.0, P)
    assert trechos == [(0.0, pytest.approx(2.1)), (pytest.approx(2.9), 4.0)]
