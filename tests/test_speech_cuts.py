"""Etapa 4: cortes de erros de fala marcados pelo LLM (sempre mockado aqui)."""

import pytest

from src.config import Settings, get_settings
from src.cuts import (
    CutParams,
    apply_cuts,
    format_transcript,
    keep_segments,
    removal_spans,
    speech_error_cuts,
    validate_speech_cuts,
)
from src.llm import client
from src.llm.schemas import CorteFala, CortesFala
from src.transcribe import Palavra, Transcricao

P = CutParams()
FRAME = 1 / 30


def frase(*itens) -> list[Palavra]:
    return [Palavra(indice=i, texto=t, inicio=a, fim=b) for i, (t, a, b) in enumerate(itens)]


# "hoje eu vou... hoje a gente vai falar de café" — falso começo sem pausa longa
FALSO_COMECO = frase(
    ("hoje", 1.00, 1.25),
    ("eu", 1.25, 1.40),
    ("vou...", 1.40, 1.80),
    ("hoje", 2.00, 2.25),
    ("a", 2.25, 2.30),
    ("gente", 2.30, 2.60),
    ("vai", 2.60, 2.80),
    ("falar", 2.80, 3.20),
    ("de", 3.20, 3.30),
    ("café", 3.30, 3.80),
)


def S() -> Settings:
    return Settings(cache_dir=get_settings().cache_dir)


def fake_llm(monkeypatch, cortes: list[tuple[int, int]], calls: list | None = None):
    def run(prompt_name, input, schema, settings=None, **kw):
        if calls is not None:
            calls.append((prompt_name, input))
        return CortesFala(
            cortes=[CorteFala(indice_inicio=a, indice_fim=b, motivo="teste") for a, b in cortes]
        )

    monkeypatch.setattr(client, "run_structured", run)


# ------------------------------------------------------------------ formato e validação


def test_transcript_format_has_index_time_and_word():
    linhas = format_transcript(FALSO_COMECO[:2]).splitlines()
    assert linhas == ["0\t1.00\thoje", "1\t1.25\teu"]


def test_valid_cuts_are_sorted_and_adjacent_ones_merged():
    assert validate_speech_cuts([(3, 3), (0, 1), (2, 2)], FALSO_COMECO, 10.0, P) == [(0, 3)]


@pytest.mark.parametrize(
    "cortes",
    [[(0, 99)], [(-1, 2)], [(5, 2)], [(0, 3), (2, 4)]],
    ids=["indice-inexistente", "negativo", "invertido", "sobreposto"],
)
def test_invalid_response_is_discarded(cortes):
    assert validate_speech_cuts(cortes, FALSO_COMECO, 10.0, P) == []


def test_removing_more_than_limit_is_discarded():
    # remover tudo (2,8 s de 4 s = 70%) passa do limite de 30%
    assert validate_speech_cuts([(0, 9)], FALSO_COMECO, 4.0, P) == []
    assert validate_speech_cuts([(0, 9)], FALSO_COMECO, 4.0, CutParams(max_corte_fala=0.9))


def test_removal_span_does_not_invade_neighbors():
    palavras = frase(("a", 1.0, 1.5), ("b", 1.4, 2.0), ("c", 1.9, 2.5))  # sobreposições
    assert removal_spans(palavras, [(1, 1)]) == [(1.5, 1.9)]


# ------------------------------------------------------------------ trechos


def test_false_start_without_pause_is_removed():
    trechos = keep_segments(FALSO_COMECO, [], 5.0, P, remover=[(0, 2)])
    assert len(trechos) == 1
    inicio, fim = trechos[0]
    assert inicio >= 1.80 - 1e-9  # nada de "hoje eu vou" sobra
    assert inicio <= 2.00  # o "hoje" certo não é cortado
    assert fim == pytest.approx(3.9, abs=FRAME)


def test_repetition_in_the_middle_splits_the_segment():
    palavras = frase(("o", 1.0, 1.1), ("o", 1.15, 1.25), ("o", 1.3, 1.4), ("problema", 1.45, 2.0))
    trechos = keep_segments(palavras, [], 5.0, P, remover=[(0, 1)])
    assert trechos[0][0] >= 1.25 - 1e-9 and trechos[0][0] <= 1.3


def test_without_cuts_behaves_like_etapa_3():
    assert keep_segments(FALSO_COMECO, [], 5.0, P) == keep_segments(
        FALSO_COMECO, [], 5.0, P, remover=[]
    )


# ------------------------------------------------------------------ chamada ao LLM


def test_llm_receives_indexed_transcript_and_result_is_used(monkeypatch):
    calls: list = []
    fake_llm(monkeypatch, [(0, 2)], calls)
    assert speech_error_cuts(FALSO_COMECO, 5.0, P, S(), "x.mp4") == [(0, 2)]
    prompt_name, texto = calls[0]
    assert prompt_name == "cortes_fala"
    assert texto.splitlines()[3] == "3\t2.00\thoje"


def test_llm_result_is_cached(monkeypatch):
    calls: list = []
    fake_llm(monkeypatch, [(0, 2)], calls)
    speech_error_cuts(FALSO_COMECO, 5.0, P, S())
    speech_error_cuts(FALSO_COMECO, 5.0, P, S())
    assert len(calls) == 1


def test_changing_model_does_not_reuse_cache(monkeypatch):
    calls: list = []
    fake_llm(monkeypatch, [(0, 2)], calls)
    base = get_settings().cache_dir
    speech_error_cuts(FALSO_COMECO, 5.0, P, Settings(cache_dir=base, llm_model="openai:a"))
    speech_error_cuts(FALSO_COMECO, 5.0, P, Settings(cache_dir=base, llm_model="anthropic:b"))
    assert len(calls) == 2


def test_llm_failure_falls_back_to_silence_only(caplog):
    # o conftest já faz run_structured lançar LLMError
    assert speech_error_cuts(FALSO_COMECO, 5.0, P, S(), "x.mp4") == []
    assert "LLM indisponível" in caplog.text


def test_invalid_llm_answer_falls_back_and_warns(monkeypatch, caplog):
    fake_llm(monkeypatch, [(0, 50)])
    assert speech_error_cuts(FALSO_COMECO, 5.0, P, S(), "x.mp4") == []
    assert "índices inválidos" in caplog.text


def test_empty_transcript_does_not_call_llm(monkeypatch):
    calls: list = []
    fake_llm(monkeypatch, [], calls)
    assert speech_error_cuts([], 5.0, P, S()) == []
    assert calls == []


# ------------------------------------------------------------------ pipeline


@pytest.fixture
def clip(tmp_path):
    from tests.conftest import make_video

    return make_video(tmp_path / "fala.mp4", duration=5.0)


def fake_transcriber(path, settings=None):
    return Transcricao(arquivo_hash="x", modelo="fake", palavras=FALSO_COMECO)


def test_apply_cuts_removes_llm_marked_words(clip, monkeypatch):
    from src.clips import project_from_files

    fake_llm(monkeypatch, [(0, 2)])
    project = apply_cuts(project_from_files([clip]), P, transcriber=fake_transcriber)
    trechos = project.timeline.clipes[0].trechos
    assert all(a >= 1.80 - 1e-9 for a, _ in trechos)


def test_apply_cuts_without_llm_option_never_calls_it(clip, monkeypatch):
    from src.clips import project_from_files

    calls: list = []
    fake_llm(monkeypatch, [(0, 2)], calls)
    apply_cuts(project_from_files([clip]), P, transcriber=fake_transcriber, cortes_fala=False)
    assert calls == []


def test_apply_cuts_llm_error_keeps_etapa_3_result(clip, monkeypatch):
    from src.clips import project_from_files

    sem_llm = apply_cuts(
        project_from_files([clip]), P, transcriber=fake_transcriber, cortes_fala=False
    )
    com_falha = apply_cuts(project_from_files([clip]), P, transcriber=fake_transcriber)
    assert com_falha.timeline.clipes[0].trechos == sem_llm.timeline.clipes[0].trechos


# ------------------------------------------------------------------ vale de energia


def _env(pontos: dict[float, float], dur: float = 3.0, base: float = 3000.0):
    import numpy as np

    from src.cuts import Envelope

    rms = np.full(int(dur / 0.01) + 1, base, dtype=np.float32)
    for t, v in pontos.items():
        rms[round(t / 0.01)] = v
    return Envelope(0.01, rms)


def test_valley_end_of_cut_takes_last_quiet_point_before_next_word():
    # vale fundo em 0.96, cauda da vogal em 0.98-1.02, vale em 1.04, próxima palavra depois
    env = _env({0.96: 346, 0.98: 900, 1.00: 1000, 1.02: 950, 1.04: 546})
    assert env.valley(0.95, 0.15, lado="ultimo") == pytest.approx(1.04)


def test_valley_start_of_cut_takes_first_quiet_point():
    env = _env({0.96: 346, 1.04: 546})
    assert env.valley(0.95, 0.15, lado="primeiro") == pytest.approx(0.96)


def test_valley_search_is_mostly_forward():
    # vale mais fundo 0.1 s ANTES do timestamp não conta (seria antes da última sílaba)
    env = _env({0.85: 10, 1.02: 300})
    assert env.valley(0.95, 0.15) == pytest.approx(1.02)


def test_valley_respects_limits_and_empty_window():
    env = _env({1.05: 10})
    assert env.valley(0.95, 0.15, hi=1.0) != pytest.approx(1.05)
    assert env.valley(0.95, 0.15, lo=2.5) == 0.95


def test_removal_span_borders_leave_the_fade_inside_the_silence():
    # silêncio 1.42-1.52 depois de "hoje"; silêncio 1.83-1.93 antes de "vou"
    quietos = {round(1.42 + k * 0.01, 2): 100 for k in range(11)}
    quietos |= {round(1.83 + k * 0.01, 2): 100 for k in range(11)}
    env = _env(quietos)
    palavras = frase(("hoje", 1.0, 1.4), ("eu", 1.4, 1.8), ("vou", 1.8, 2.1))
    ((inicio, fim),) = removal_spans(palavras, [(1, 1)], env, 0.15, fps=30, folga=0.03)
    assert inicio == pytest.approx(1.5)  # fim do 1º silêncio (na grade): fade cabe nele
    assert fim == pytest.approx(1.8333, abs=1e-3)  # começo do 2º silêncio (na grade)


def test_narrow_silence_prefers_leftover_over_eating_the_kept_word():
    env = _env({1.45: 100, 1.85: 100})  # vales de 1 amostra só (10 ms)
    palavras = frase(("hoje", 1.0, 1.4), ("eu", 1.4, 1.8), ("vou", 1.8, 2.1))
    ((inicio, fim),) = removal_spans(palavras, [(1, 1)], env, 0.15, fps=30, folga=0.03)
    assert inicio >= 1.45  # não recua para dentro de "hoje"
    assert fim <= 1.85  # não avança para dentro de "vou"


def test_without_valley_uses_whisper_time_rounded_into_the_cut():
    import numpy as np

    from src.cuts import Envelope

    env = Envelope(0.01, np.full(400, 1000.0, dtype=np.float32))
    palavras = frase(("hoje", 1.0, 1.41), ("eu", 1.41, 1.79), ("vou", 1.79, 2.1))
    ((inicio, fim),) = removal_spans(palavras, [(1, 1)], env, 0.15, fps=30)
    assert inicio == pytest.approx(1.4333, abs=1e-3)  # ceil(1.41)
    assert fim == pytest.approx(1.7667, abs=1e-3)  # floor(1.79)


def test_energy_envelope_from_real_audio(tmp_path):
    import subprocess

    from src.cuts import energy_envelope

    clip = tmp_path / "tom.mp4"
    expr = "0.5*sin(2*PI*440*t)*between(t,1,2)"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
        + ["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=3"]
        + ["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d=3"]
        + ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(clip)],
        check=True,
        capture_output=True,
    )
    env = energy_envelope(clip)
    assert env.rms[50] < 0.05 * env.rms[150]  # 0,5 s (silêncio) x 1,5 s (tom)
    assert env.valley(1.5, 0.6, lado="ultimo") == pytest.approx(2.1, abs=0.05)
    again = energy_envelope(clip)  # do cache
    assert again.rms.shape == env.rms.shape


def test_energy_envelope_without_audio_is_none(tmp_path):
    from src.cuts import energy_envelope
    from tests.conftest import make_video

    assert energy_envelope(make_video(tmp_path / "mudo.mp4", audio=False)) is None


def test_speech_error_cuts_never_raises(monkeypatch, caplog):
    import src.cuts as cuts

    def boom(*a, **k):
        raise OSError("disco cheio")

    monkeypatch.setattr(cuts, "_speech_error_cuts", boom)
    assert speech_error_cuts(FALSO_COMECO, 5.0, P, S(), "x.mp4") == []
    assert "disco cheio" in caplog.text


@pytest.mark.parametrize("variacao", [0.0, 300.0])
def test_no_real_valley_in_continuous_speech_returns_whisper_time(variacao):
    import numpy as np

    from src.cuts import Envelope

    rng = np.random.default_rng(0)
    rms = (np.full(1000, 1000.0) + rng.uniform(0, variacao, 1000)).astype(np.float32)
    env = Envelope(0.01, rms)  # fala vozeada contínua: variação < 6 dB, nenhum vale
    assert env.valley(5.0, 0.15, lado="ultimo") == 5.0
    assert env.valley(5.0, 0.15, lado="primeiro") == 5.0


def test_quiet_run_picks_first_or_last_silent_stretch():
    env = _env({0.96: 300, 0.97: 300, 1.04: 400, 1.05: 400, 1.06: 400})
    assert env.quiet_run(0.95, 0.15, lado="primeiro") == (pytest.approx(0.96), pytest.approx(0.97))
    assert env.quiet_run(0.95, 0.15, lado="ultimo") == (pytest.approx(1.04), pytest.approx(1.06))
