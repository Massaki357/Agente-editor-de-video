"""Parte 4, Etapa 0: seleção de frases inteiras para legendas de destaque."""

from types import SimpleNamespace

import pytest

import src.highlight_captions.planner as planner
from src.config import Settings
from src.highlight_captions.planner import (
    DestaqueParams,
    FraseCandidata,
    plan_highlights,
    plan_highlights_project,
    segmentar_frases,
    validate_highlights,
)
from src.images import ItemImagem, PalavraGlobal, PlanoImagens, load_plan, save_plan
from src.llm import client
from src.llm.schemas import DestaqueSugerido, PlanoDestaques
from src.pipeline import PipelineOptions, image_plan
from src.project import Clip, ClipMeta, Project, Timeline
from src.transcribe import Palavra


def _words(*rows: tuple[str, float, float, int, int]) -> list[PalavraGlobal]:
    return [
        PalavraGlobal(i, clip, Palavra(indice=i, texto=text, inicio=start, fim=end), segment)
        for i, (text, start, end, clip, segment) in enumerate(rows)
    ]


def _phrase(first: int, last: int, start: float, end: float, text: str, clip: int = 0):
    return FraseCandidata(
        clipe=clip,
        segmento=0,
        trecho_inicio_palavra=first,
        trecho_fim_palavra=last,
        inicio=start,
        fim=end,
        texto=text,
    )


def _suggest(first: int, last: int, text: str) -> DestaqueSugerido:
    return DestaqueSugerido(
        trecho_inicio_palavra=first,
        trecho_fim_palavra=last,
        texto=text,
        motivo="frase de impacto",
    )


def test_segmentation_respects_punctuation_pause_clip_and_internal_cut():
    words = _words(
        ("Medimos", 0, 0.4, 0, 0),
        ("o", 0.5, 0.6, 0, 0),
        ("problema.", 0.7, 1.6, 0, 0),
        ("Agora", 2.5, 2.8, 0, 0),
        ("sabemos", 2.9, 3.5, 0, 0),
        ("a", 3.6, 3.8, 0, 1),
        ("solução.", 3.9, 4.5, 0, 1),
        ("Mudou", 5.0, 5.4, 1, 0),
        ("tudo.", 5.5, 6.7, 1, 0),
    )
    phrases = segmentar_frases(words)
    assert [(f.trecho_inicio_palavra, f.trecho_fim_palavra) for f in phrases] == [
        (0, 2),
        (3, 4),
        (5, 6),
        (7, 8),
    ]
    assert [f.texto for f in phrases] == [
        "Medimos o problema.",
        "Agora sabemos",
        "a solução.",
        "Mudou tudo.",
    ]
    assert phrases[1].segmento == 0 and phrases[2].segmento == 1


def test_validation_uses_exact_phrase_text_timing_density_and_no_adjacent_phrases():
    phrases = [
        _phrase(0, 3, 0, 2.4, "A perda caiu pela metade."),
        _phrase(4, 7, 11, 13.0, "Agora medimos tudo."),
        _phrase(8, 11, 15, 17.3, "Cada litro tem valor."),
        _phrase(12, 15, 25, 27.5, "A reserva atende a todos.", clip=1),
        _phrase(16, 20, 38, 46, "Uma frase longa demais para destaque."),
    ]
    raw = [
        _suggest(0, 2, "A perda caiu pela"),  # borda parcial
        _suggest(0, 3, "A perda caiu pela metade"),  # pontuação reescrita
        _suggest(0, 3, phrases[0].texto),
        _suggest(4, 7, phrases[1].texto),  # frase consecutiva, apesar de 11 s
        _suggest(8, 11, phrases[2].texto),
        _suggest(12, 15, phrases[3].texto),  # frase consecutiva com a anterior
        _suggest(16, 20, phrases[4].texto),  # excede o teto
    ]
    valid = validate_highlights(raw, phrases)
    assert [(i.trecho_inicio_palavra, i.trecho_fim_palavra) for i in valid] == [(0, 3), (8, 11)]
    assert [(i.inicio, i.fim, i.texto) for i in valid] == [
        (0, 2.4, phrases[0].texto),
        (15, 17.3, phrases[2].texto),
    ]
    assert [i.id for i in valid] == [0, 1]
    with pytest.raises(ValueError):
        DestaqueParams(duracao_max=0.1)


def test_validation_reserves_hold_before_next_highlight():
    phrases = [
        _phrase(0, 2, 0, 3, "Uma frase impactante."),
        _phrase(4, 6, 5, 7, "Outra frase forte."),
    ]
    suggested = [_suggest(0, 2, phrases[0].texto), _suggest(4, 6, phrases[1].texto)]
    result = validate_highlights(
        suggested, phrases, DestaqueParams(duracao_permanencia=5, intervalo_min=4)
    )
    assert len(result) == 1 and result[0].texto == phrases[0].texto


def test_llm_receives_full_phrases_and_reuses_cache(tmp_path, monkeypatch):
    phrases = [
        _phrase(0, 3, 0, 2.4, "A perda caiu pela metade."),
        _phrase(4, 8, 8, 16, "Longa demais para uma legenda de destaque."),
    ]
    settings = Settings(cache_dir=tmp_path / "cache")
    calls = []

    def run(name, payload, schema, settings=None):
        calls.append((name, payload, schema))
        return PlanoDestaques(destaques=[_suggest(0, 3, phrases[0].texto)])

    monkeypatch.setattr(client, "run_structured", run)
    expected = [_suggest(0, 3, phrases[0].texto)]
    assert planner.suggest_highlights(phrases, settings=settings) == expected
    assert planner.suggest_highlights(phrases, settings=settings) == expected
    assert len(calls) == 1 and calls[0][0] == "plano_destaques"
    assert calls[0][2] is PlanoDestaques
    assert [f["texto"] for f in calls[0][1]["frases"]] == [
        phrase.texto for phrase in phrases
    ]
    assert [f["texto"] for f in calls[0][1]["roteiro_completo"]] == [
        phrase.texto for phrase in phrases
    ]


def test_llm_failure_preserves_other_plan_items_and_legacy_plan(tmp_path, monkeypatch):
    words = _words(
        ("A", 0, 0.2, 0, 0),
        ("perda", 0.3, 0.8, 0, 0),
        ("caiu", 0.9, 1.3, 0, 0),
        ("pela", 1.4, 1.7, 0, 0),
        ("metade.", 1.8, 2.4, 0, 0),
    )
    original = PlanoImagens(
        assinatura="abc",
        itens=[
            ItemImagem(id=0, indice=1, clipe=0, palavra="perda", query="loss", inicio=1, duracao=2)
        ],
    )

    def fail(*args, **kwargs):
        raise client.LLMError("sem chave")

    monkeypatch.setattr(client, "run_structured", fail)
    updated = plan_highlights(words, original, settings=Settings(cache_dir=tmp_path / "cache"))
    assert updated is not original and updated.destaques == []
    assert updated.itens == original.itens and original.destaques == []
    path = save_plan(updated, tmp_path / "plano.json")
    assert load_plan(path).destaques == []
    assert PlanoImagens.model_validate({"assinatura": "antigo"}).destaques == []


def test_project_planning_uses_global_out_times_without_crossing_clips(tmp_path, monkeypatch):
    meta = ClipMeta(duracao=5, largura=320, altura=180, fps=30, tem_audio=True)
    paths = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    project = Project(
        timeline=Timeline(
            clipes=[Clip(arquivo=str(path), trechos=[(0, 5)], meta=meta) for path in paths]
        )
    )
    project.timeline.recalcular_offsets()
    transcript = SimpleNamespace(
        palavras=[
            Palavra(indice=i, texto=text, inicio=i * 0.6, fim=i * 0.6 + 0.5)
            for i, text in enumerate(("A", "perda", "caiu", "pela", "metade."))
        ]
    )
    monkeypatch.setattr(
        planner,
        "suggest_highlights",
        lambda phrases, *a, **k: [_suggest(5, 9, "A perda caiu pela metade.")],
    )
    result = plan_highlights_project(project, PlanoImagens(assinatura="x"), lambda path: transcript)
    assert len(result.destaques) == 1
    (item,) = result.destaques
    assert item.clipe == 1 and item.inicio == 5.0 and item.fim == pytest.approx(7.9)
    assert (item.trecho_inicio_palavra, item.trecho_fim_palavra) == (5, 9)


def test_project_drops_a_phrase_whose_word_crosses_an_internal_cut(tmp_path, monkeypatch):
    """Whisper pode marcar uma palavra de um lado ao outro da emenda."""
    meta = ClipMeta(duracao=6, largura=320, altura=180, fps=30, tem_audio=True)
    project = Project(
        timeline=Timeline(
            clipes=[Clip(arquivo=str(tmp_path / "fala.mp4"), trechos=[(0, 2), (4, 6)], meta=meta)]
        )
    )
    project.timeline.recalcular_offsets()
    transcript = SimpleNamespace(
        palavras=[
            Palavra(indice=0, texto="Uma", inicio=0.2, fim=0.5),
            Palavra(indice=1, texto="frase", inicio=0.6, fim=1.2),
            Palavra(indice=2, texto="cruzada.", inicio=1.5, fim=4.5),
        ]
    )
    seen = []

    def suggest(phrases, *args, **kwargs):
        seen.extend(phrases)
        return [_suggest(0, 2, "Uma frase cruzada.")]

    monkeypatch.setattr(planner, "suggest_highlights", suggest)
    result = plan_highlights_project(project, PlanoImagens(assinatura="x"), lambda path: transcript)
    assert seen == [] and result.destaques == []


def test_project_drops_phrase_without_room_for_hold_before_seam(tmp_path, monkeypatch):
    words = _words(
        ("Algo", 0.2, 0.6, 0, 0),
        ("mudou", 0.7, 1.1, 0, 0),
        ("hoje.", 1.2, 1.8, 0, 0),
    )
    seen = []

    def suggest(phrases, *args, **kwargs):
        seen.extend(phrases)
        return [_suggest(0, 2, "Algo mudou hoje.")]

    monkeypatch.setattr(planner, "suggest_highlights", suggest)
    plano = plan_highlights(
        words,
        PlanoImagens(assinatura="x"),
        limites_segmentos=[(0, 2)],
    )
    assert len(seen) == 1 and plano.destaques == []


def test_validation_accepts_literal_excerpt_of_at_most_five_words():
    words = _words(
        ("Mas", 0.0, 0.2, 0, 0),
        ("quase", 0.3, 0.5, 0, 0),
        ("metade", 0.6, 0.9, 0, 0),
        ("da", 1.0, 1.1, 0, 0),
        ("água", 1.2, 1.5, 0, 0),
        ("se", 1.6, 1.7, 0, 0),
        ("perdia", 1.8, 2.2, 0, 0),
        ("ali.", 2.3, 2.6, 0, 0),
    )
    phrase = _phrase(0, 7, 0, 2.6, "Mas quase metade da água se perdia ali.")
    suggestions = [
        _suggest(2, 6, "metade da água se perdia"),
        _suggest(1, 6, "quase metade da água se perdia"),
    ]
    valid = validate_highlights(
        suggestions, [phrase], palavras=words, limites_segmentos=[(0, 5)]
    )
    assert len(valid) == 1
    assert (valid[0].trecho_inicio_palavra, valid[0].trecho_fim_palavra) == (2, 6)
    assert valid[0].texto == "metade da água se perdia"


def test_old_highlight_plan_is_replanned_when_hold_changes(tmp_path, monkeypatch):
    import src.pipeline as pipeline
    from src.images import timeline_signature

    project = Project(timeline=Timeline(clipes=[Clip(arquivo=str(tmp_path / "fala.mp4"))]))
    old = PlanoImagens(assinatura=timeline_signature(project))
    calls = []

    def replan(project, plano, transcriber, params, settings):
        calls.append(params.duracao_permanencia)
        return plano.model_copy(
            update={
                "versao_destaques": 2,
                "duracao_permanencia_destaques": params.duracao_permanencia,
            }
        )

    monkeypatch.setattr(pipeline, "plan_highlights_project", replan)
    opts = PipelineOptions(
        imagens=False, zooms=False, legendas_continuas=False, legendas_destaque=True
    )
    current = image_plan(project, opts, old)
    assert calls == [1.2]
    assert image_plan(project, opts, current) is current
    changed = opts.model_copy(
        update={"estilo_destaque": opts.estilo_destaque.model_copy(
            update={"duracao_permanencia": 2.0}
        )}
    )
    image_plan(project, changed, current)
    assert calls == [1.2, 2.0]
