"""Parte 3, Etapa 0: frases elegíveis, exclusividade e limites do B-roll."""

from types import SimpleNamespace

import pytest

import src.broll.planner as planner
from src.broll.planner import (
    BrollParams,
    TrechoFala,
    plan_broll,
    plan_broll_project,
    segmentar_fala,
    validate_broll,
)
from src.config import Settings
from src.images import ItemImagem, PalavraGlobal, PlanoImagens, load_plan, save_plan
from src.llm import client
from src.llm.schemas import BrollSugerido, PlanoBroll
from src.project import Clip, ClipMeta, Project, Timeline
from src.transcribe import Palavra


def _words(*rows: tuple[str, float, float, int]) -> list[PalavraGlobal]:
    return [
        PalavraGlobal(i, clip, Palavra(indice=i, texto=text, inicio=start, fim=end))
        for i, (text, start, end, clip) in enumerate(rows)
    ]


def _segment(start_word: int, end_word: int, start: float, end: float, clip: int = 0):
    return TrechoFala(
        clipe=clip,
        trecho_inicio_palavra=start_word,
        trecho_fim_palavra=end_word,
        inicio=start,
        fim=end,
        texto=f"frase {start_word} a {end_word}",
    )


def _suggest(start: int, end: int, duration: float = 3.0):
    return BrollSugerido(
        trecho_inicio_palavra=start,
        trecho_fim_palavra=end,
        query="coffee harvest video",
        duracao_max=duration,
        motivo="mostra a colheita",
    )


def test_sentences_end_at_punctuation_pause_or_clip_seam():
    words = _words(
        ("Hoje", 0.0, 0.3, 0),
        ("colhemos", 0.4, 1.0, 0),
        ("café.", 1.1, 1.6, 0),
        ("Depois", 2.5, 2.8, 0),
        ("torramos", 2.9, 3.6, 0),
        ("os", 3.7, 3.9, 0),
        ("grãos", 4.0, 4.5, 0),
        ("Aqui", 5.3, 5.8, 1),
        ("servimos.", 5.9, 6.7, 1),
    )
    parts = segmentar_fala(words)
    assert [(t.trecho_inicio_palavra, t.trecho_fim_palavra, t.clipe) for t in parts] == [
        (0, 2, 0),
        (3, 6, 0),
        (7, 8, 1),
    ]
    assert (parts[0].inicio, parts[0].fim) == (0.0, 1.6)
    assert parts[1].texto == "Depois torramos os grãos"


def test_validation_enforces_whole_words_duration_density_and_return_to_camera():
    parts = [
        _segment(0, 3, 0, 2.2),
        _segment(4, 7, 4, 6.1),
        _segment(8, 11, 9, 11.4),
        _segment(12, 15, 19, 21.5, clip=1),
        _segment(16, 20, 29, 33.5),  # acima do teto de 4 s
    ]
    raw = [
        _suggest(0, 2),  # borda no meio da frase: inválido
        _suggest(0, 3, duration=30),  # LLM exagerou: código usa os 2,2 s exatos
        _suggest(4, 7),  # muito perto do primeiro
        _suggest(8, 11),
        _suggest(12, 15),
        _suggest(16, 20),  # frase longa: descartada
    ]
    valid = validate_broll(raw, parts, imagens=[(9.5, 10.5)])
    assert [(i.trecho_inicio_palavra, i.trecho_fim_palavra) for i in valid] == [(0, 3), (12, 15)]
    assert [(i.inicio, i.fim) for i in valid] == [(0, 2.2), (19, 21.5)]
    assert all(i.duracao_max <= 4 for i in valid)
    assert validate_broll([_suggest(0, 3, duration=0.5)], parts) == []


def test_density_and_return_gap_are_both_configurable():
    parts = [_segment(0, 3, 0, 3.0), _segment(4, 7, 5.0, 7.0), _segment(8, 11, 9.0, 11.0)]
    raw = [_suggest(0, 3), _suggest(4, 7), _suggest(8, 11)]
    assert [i.inicio for i in validate_broll(raw, parts)] == [0, 9]
    params = BrollParams(intervalo_min=1, retorno_min=3)
    assert [i.inicio for i in validate_broll(raw, parts, params=params)] == [0, 9]
    with pytest.raises(ValueError):
        BrollParams(duracao_max=1.0)


def test_word_boundaries_keep_submillisecond_precision():
    phrase = _segment(2, 7, 1.23456, 3.45678)
    (item,) = validate_broll([_suggest(2, 7)], [phrase])
    assert item.inicio == phrase.inicio
    assert item.fim == pytest.approx(phrase.fim, abs=1e-12)


def test_llm_receives_full_sentences_and_reuses_cache(tmp_path, monkeypatch):
    parts = [_segment(0, 3, 0, 2.2), _segment(4, 7, 10, 12.5)]
    settings = Settings(cache_dir=tmp_path / "cache")
    calls = []

    def run(name, payload, schema, settings=None):
        calls.append((name, payload, schema))
        return PlanoBroll(broll=[_suggest(4, 7)])

    monkeypatch.setattr(client, "run_structured", run)
    assert planner.suggest_broll(parts, [(0.5, 1.0)], settings=settings) == [_suggest(4, 7)]
    assert planner.suggest_broll(parts, [(0.5, 1.0)], settings=settings) == [_suggest(4, 7)]
    assert len(calls) == 1 and calls[0][0] == "plano_broll"
    assert calls[0][2] is PlanoBroll
    assert [t["trecho_inicio_palavra"] for t in calls[0][1]["trechos"]] == [4]


def test_failure_returns_empty_without_corrupting_the_existing_plan(tmp_path, monkeypatch):
    parts = [_segment(0, 3, 0, 2.2)]
    settings = Settings(cache_dir=tmp_path / "cache")

    def fail(*args, **kwargs):
        raise client.LLMError("sem chave")

    monkeypatch.setattr(client, "run_structured", fail)
    assert planner.suggest_broll(parts, [], settings=settings) == []
    photo = ItemImagem(
        id=0, indice=0, clipe=0, palavra="café", query="coffee", inicio=10, duracao=2
    )
    original = PlanoImagens(assinatura="abc", itens=[photo])
    monkeypatch.setattr(planner, "suggest_broll", lambda *a, **k: [_suggest(0, 3)])
    words = _words(
        ("Aqui", 0.0, 0.5, 0),
        ("colhemos", 0.6, 1.0, 0),
        ("o", 1.1, 1.3, 0),
        ("café.", 1.4, 2.2, 0),
    )
    updated = plan_broll(words, original, settings=settings)
    assert len(updated.broll) == 1 and original.broll == []
    path = save_plan(updated, tmp_path / "plano.json")
    loaded = load_plan(path)
    assert loaded is not None and loaded.broll == updated.broll
    assert loaded.itens == original.itens and loaded.assinatura == "abc"
    # Planos salvos antes desta etapa continuam legíveis.
    old = PlanoImagens.model_validate({"assinatura": "antigo", "itens": [], "zooms": []})
    assert old.broll == []


def test_image_and_cutaway_never_share_the_same_interval(monkeypatch):
    words = _words(
        ("A", 0, 0.2, 0),
        ("colheita", 0.3, 1.0, 0),
        ("começou", 1.1, 1.7, 0),
        ("hoje.", 1.8, 2.3, 0),
    )
    photo = ItemImagem(
        id=0,
        indice=1,
        clipe=0,
        palavra="colheita",
        query="harvest",
        inicio=1.0,
        duracao=1.5,
        ativa=True,
    )
    monkeypatch.setattr(planner, "suggest_broll", lambda *a, **kw: [_suggest(0, 3)])
    assert plan_broll(words, PlanoImagens(assinatura="x", itens=[photo])).broll == []
    photo.ativa = False
    assert len(plan_broll(words, PlanoImagens(assinatura="x", itens=[photo])).broll) == 1


def test_project_planning_uses_global_final_times_without_crossing_clips(tmp_path, monkeypatch):
    paths = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    meta = ClipMeta(duracao=2.0, largura=320, altura=180, fps=30, tem_audio=True)
    timeline = Timeline(
        clipes=[Clip(arquivo=str(path), trechos=[(0.0, 2.0)], meta=meta) for path in paths]
    )
    timeline.recalcular_offsets()
    project = Project(timeline=timeline)
    transcript = SimpleNamespace(
        palavras=[
            Palavra(indice=i, texto=text, inicio=i * 0.45, fim=i * 0.45 + 0.35)
            for i, text in enumerate(("Aqui", "colhemos", "o", "café."))
        ]
    )
    monkeypatch.setattr(planner, "suggest_broll", lambda *a, **kw: [_suggest(4, 7)])
    result = plan_broll_project(project, PlanoImagens(assinatura="x"), lambda path: transcript)
    assert len(result.broll) == 1
    item = result.broll[0]
    assert item.clipe == 1 and item.inicio == 2.0
    assert item.trecho_inicio_palavra == 4 and item.trecho_fim_palavra == 7


def test_sentences_never_cross_an_internal_cut_of_one_clip(tmp_path):
    from src.images import global_words

    timeline = Timeline(
        clipes=[
            Clip(
                arquivo=str(tmp_path / "a.mp4"),
                trechos=[(0.0, 2.0), (5.0, 7.0)],
                meta=ClipMeta(duracao=7.0, largura=320, altura=180, fps=30, tem_audio=True),
            )
        ]
    )
    timeline.recalcular_offsets()
    project = Project(timeline=timeline)
    starts = [0.0, 0.45, 0.9, 1.35, 5.0, 5.45, 5.9, 6.35]
    transcript = SimpleNamespace(
        palavras=[
            Palavra(indice=i, texto=f"palavra{i}", inicio=t, fim=t + 0.35)
            for i, t in enumerate(starts)
        ]
    )
    words = global_words(project, lambda path: transcript)
    assert [w.segmento for w in words] == [0] * 4 + [1] * 4
    assert [round(w.palavra.inicio, 2) for w in words[4:]] == [2.0, 2.45, 2.9, 3.35]
    assert [(s.trecho_inicio_palavra, s.trecho_fim_palavra) for s in segmentar_fala(words)] == [
        (0, 3),
        (4, 7),
    ]
