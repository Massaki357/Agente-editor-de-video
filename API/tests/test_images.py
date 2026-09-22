"""Etapa 8: imagens sobre a fala (LLM, busca e geometria sempre falsos aqui)."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import src.images as images
from src.cuts import TimeMap
from src.images import (
    Candidato,
    ImageParams,
    ItemImagem,
    Overlay,
    PalavraGlobal,
    PlanoImagens,
    build_overlays,
    candidate_zones,
    choose_zone,
    prepare_overlay,
    search_images,
    suggest,
    validate_suggestions,
)
from src.llm import client
from src.llm.schemas import ImagemSugerida, PlanoCriativo, ZoomSugerido
from src.project import Clip, Timeline
from src.transcribe import Palavra

P = ImageParams()
SAIDA = (1080, 1920)


def _timeline() -> Timeline:
    t = Timeline(
        clipes=[
            Clip(arquivo="a.mp4", trechos=[(0.0, 10.0)]),  # t_out 0–10
            Clip(arquivo="b.mp4", trechos=[(0.0, 10.0)]),  # t_out 10–20
        ]
    )
    t.recalcular_offsets()
    return t


def _palavras(*itens) -> list[PalavraGlobal]:
    """(texto, t_out, clipe) → palavras globais."""
    return [
        PalavraGlobal(i, clipe, Palavra(indice=i, texto=texto, inicio=t, fim=t + 0.3))
        for i, (texto, t, clipe) in enumerate(itens)
    ]


PALAVRAS = _palavras(
    ("café", 1.0, 0), ("bom", 1.5, 0), ("cachorro", 2.0, 0), ("praia", 6.0, 0),
    ("ovos", 9.4, 0), ("gato", 12.0, 1), ("gato", 16.0, 1),
)  # fmt: skip


def item(id: int = 0, inicio: float = 1, ativa: bool = True, palavra: str = "café") -> ItemImagem:
    return ItemImagem(
        id=id, indice=id, clipe=0, palavra=palavra, query=palavra, inicio=inicio, duracao=2,
        candidatos=[cand(id)], ativa=ativa,
    )  # fmt: skip


def cand(i: int = 0) -> Candidato:
    return Candidato(
        fonte="pexels", id=str(i), url=f"https://x/{i}.jpg", miniatura=f"https://x/{i}m.jpg"
    )


# ------------------------------------------------------------------ validação


def test_valid_suggestion_starts_before_the_word_and_is_clamped():
    itens = validate_suggestions(
        [(0, "café", "coffee cup", 9.0)], PALAVRAS, TimeMap(_timeline()), P
    )
    (item,) = itens
    assert item.inicio == pytest.approx(0.8)  # 0,2 s antes da palavra
    assert item.duracao == pytest.approx(3.0)  # duração máxima
    assert item.clipe == 0 and item.palavra == "café"


def test_density_drops_images_too_close_together():
    brutos = [(0, "café", "coffee", 2.0), (2, "cachorro", "dog", 2.0), (3, "praia", "beach", 2.0)]
    itens = validate_suggestions(brutos, PALAVRAS, TimeMap(_timeline()), P)
    assert [i.palavra for i in itens] == ["café", "praia"]  # cachorro a 1 s do café: fora


def test_image_never_crosses_the_seam():
    # "ovos" em 9,4 s (clipe a termina em 10): 0,8 s de sobra < 1,2 s mínimo → fora
    assert validate_suggestions([(4, "ovos", "eggs", 2.0)], PALAVRAS, TimeMap(_timeline()), P) == []
    # com mais espaço, é encurtada para terminar exatamente na emenda
    palavras = _palavras(("ovos", 8.4, 0))
    (item,) = validate_suggestions([(0, "ovos", "eggs", 3.0)], palavras, TimeMap(_timeline()), P)
    assert item.inicio + item.duracao == pytest.approx(10.0)


def test_same_image_in_sequence_is_dropped():
    brutos = [(5, "gato", "cute cat", 2.0), (6, "gato", "Cute  Cat", 2.0)]
    itens = validate_suggestions(brutos, PALAVRAS, TimeMap(_timeline()), P)
    assert len(itens) == 1


def test_wrong_index_is_fixed_to_a_nearby_matching_word_or_dropped():
    tm = TimeMap(_timeline())
    (item,) = validate_suggestions([(1, "cachorro", "dog", 2.0)], PALAVRAS, tm, P)
    assert item.indice == 2  # o LLM errou por 1
    assert validate_suggestions([(1, "elefante", "elephant", 2.0)], PALAVRAS, tm, P) == []
    assert validate_suggestions([(99, "café", "coffee", 2.0)], PALAVRAS, tm, P) == []


# ------------------------------------------------------------------ LLM


def fake_llm(monkeypatch, sugestoes, calls, zooms=()):
    def run(prompt_name, texto, schema, settings=None, **kw):
        calls.append((prompt_name, texto))
        return PlanoCriativo(
            imagens=[ImagemSugerida(**s) for s in sugestoes],
            zooms=[ZoomSugerido(**z) for z in zooms],
        )

    monkeypatch.setattr(client, "run_structured", run)


def test_llm_gets_the_global_transcript_and_result_is_cached(monkeypatch):
    calls: list = []
    fake_llm(
        monkeypatch,
        [{"indice": 0, "palavra": "café", "query": "coffee", "duracao": 2}],
        calls,
        zooms=[{"indice": 5, "palavra": "gato", "duracao": 1.5}],
    )
    esperado = ([(0, "café", "coffee", 2.0)], [(5, "gato", 1.5)])
    assert suggest(PALAVRAS) == esperado
    assert suggest(PALAVRAS) == esperado  # cache: uma chamada só para imagens e zooms
    assert len(calls) == 1 and calls[0][0] == "plano_criativo"
    linhas = calls[0][1].splitlines()
    assert linhas[5] == "5\t12.00\tgato"  # índice global, tempo final (clipe b)


def test_llm_failure_means_no_images_nor_zooms():
    assert suggest(PALAVRAS) == ([], [])  # o conftest faz o LLM falhar


# ------------------------------------------------------------------ busca


def test_search_falls_back_to_pixabay_and_caches(monkeypatch):
    chamadas = []
    monkeypatch.setattr(images, "_pexels", lambda q, n, s: chamadas.append("pexels") or [])
    monkeypatch.setattr(images, "_pixabay", lambda q, n, s: chamadas.append("pixabay") or [cand()])
    assert search_images("fruit basket")[0].id == "0"
    assert search_images("fruit basket")[0].id == "0"
    assert chamadas == ["pexels", "pixabay"]  # a 2ª vez veio do cache


def test_search_without_results_is_not_cached(monkeypatch):
    assert search_images("nada") == []
    monkeypatch.setattr(images, "_pexels", lambda q, n, s: [cand(7)])
    assert search_images("nada")[0].id == "7"


def test_search_errors_do_not_break(monkeypatch):
    def quebra(q, n, s):
        raise images.requests.ConnectionError("sem rede")

    monkeypatch.setattr(images, "_pexels", quebra)
    assert search_images("x") == []


# ------------------------------------------------------------------ geometria


LEGENDA = (48, 1235, 984, 187)


def test_zones_are_inside_the_frame_and_above_the_caption():
    for nome, (x, y, w, h) in candidate_zones(SAIDA, LEGENDA).items():
        assert 0 <= x and x + w <= 1080 and 0 <= y and y + h <= 1920, nome
        assert y + h <= LEGENDA[1], nome  # nenhuma zona desce até a legenda


def test_zone_crossing_the_face_or_caption_is_never_chosen():
    zonas = candidate_zones(SAIDA, LEGENDA)
    rosto_no_topo_centro = (440, 200, 200, 240)
    nome, zona = choose_zone(zonas, [rosto_no_topo_centro, LEGENDA])
    assert nome not in ("topo",)
    assert not images._intersecta(zona, rosto_no_topo_centro, 0)
    assert not images._intersecta(zona, LEGENDA, 0)


def test_largest_free_zone_wins_and_none_when_everything_is_blocked():
    zonas = candidate_zones(SAIDA, LEGENDA)
    assert choose_zone(zonas, [])[0] == "topo"
    assert choose_zone(zonas, [(0, 0, 1080, 1920)]) is None


def _foto(path: Path, w: int, h: int) -> Path:
    Image.new("RGB", (w, h), (30, 140, 200)).save(path)
    return path


def test_prepared_overlay_fits_the_zone_and_has_transparent_corners(tmp_path):
    w, h = prepare_overlay(_foto(tmp_path / "f.jpg", 1200, 800), (960, 500), tmp_path / "o.png")
    assert w <= 960 and h <= 500 and max(w / 960, h / 500) > 0.98  # ocupa a zona
    img = Image.open(tmp_path / "o.png")
    assert img.mode == "RGBA" and img.getpixel((0, 0))[3] == 0  # canto arredondado
    assert img.getpixel((w // 2, 3))[:3] == (255, 255, 255)  # borda branca


def test_sticker_uses_removed_background(tmp_path, monkeypatch):
    def recorta(origem, img, settings):
        out = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
        out.paste((200, 50, 50, 255), (100, 100, 200, 200))
        return out

    monkeypatch.setattr(images, "_sticker", recorta)
    prepare_overlay(_foto(tmp_path / "f.jpg", 400, 400), (500, 500), tmp_path / "s.png", True)
    img = Image.open(tmp_path / "s.png")
    assert img.getpixel((5, 5))[3] == 0  # fora do objeto: transparente


def test_overlays_avoid_face_and_caption(tmp_path, monkeypatch):
    monkeypatch.setattr(
        images, "download", lambda url, s=None: _foto(tmp_path / "d.jpg", 1200, 800)
    )
    plano = PlanoImagens(
        assinatura="x",
        itens=[item(0, inicio=1), item(1, inicio=5, ativa=False, palavra="off")],
    )  # fmt: skip
    rosto = (400, 180, 300, 300)
    ovs = build_overlays(plano, SAIDA, LEGENDA, lambda a, b: [rosto], tmp_path)
    assert len(ovs) == 1  # item desativado não entra
    ov = ovs[0]
    caixa = (ov.x, ov.y, ov.w, ov.h)
    assert not images._intersecta(caixa, rosto, 0) and not images._intersecta(caixa, LEGENDA, 0)
    assert (ov.inicio, ov.fim) == (1, 3)


def test_image_without_free_space_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "download", lambda url, s=None: _foto(tmp_path / "d.jpg", 100, 100))
    plano = PlanoImagens(
        assinatura="x",
        itens=[item(0)],
    )  # fmt: skip
    assert build_overlays(plano, SAIDA, LEGENDA, lambda a, b: [(0, 0, 1080, 1920)], tmp_path) == []


# ------------------------------------------------------------------ render


def test_overlay_appears_only_inside_its_interval(tmp_path):
    from src.clips import project_from_files
    from src.render import render_timeline
    from tests.test_captions import _frame, _video

    clip = _video(tmp_path / "bg.mp4", "black", 3.0)
    Image.new("RGBA", (400, 300), (255, 0, 0, 255)).save(tmp_path / "red.png")
    ov = Overlay(tmp_path / "red.png", 340, 300, 400, 300, 0.5, 2.0, 0)
    out = render_timeline(project_from_files([clip]).timeline, tmp_path / "o.mp4", overlays=[ov])

    def vermelho(t: float) -> float:
        reg = _frame(out, t)[300:600, 340:740]
        return float(((reg[..., 0] > 150) & (reg[..., 1] < 80)).mean())

    assert vermelho(0.3) == 0 and vermelho(2.5) == 0
    assert vermelho(1.0) > 0.98
    f = _frame(out, 1.0)
    fora = np.concatenate([f[:290].ravel(), f[610:].ravel()])
    assert fora.max() < 40  # nada fora da posição


# ------------------------------------------------------------------ reuso do plano


def test_saved_plan_is_reused_without_calling_the_llm(monkeypatch):
    from src.pipeline import PipelineOptions, image_plan

    project = __import__("src.project", fromlist=["Project"]).Project(timeline=_timeline())
    assinatura = images.timeline_signature(project)
    plano = PlanoImagens(assinatura=assinatura, itens=[])

    def proibido(*a, **k):
        raise AssertionError("não deveria chamar o LLM")

    monkeypatch.setattr(images, "suggest", proibido)
    assert image_plan(project, PipelineOptions(), plano) is plano

    project.timeline.substituir_trechos(0, [(0.0, 5.0)])  # os cortes mudaram
    chamados = []
    monkeypatch.setattr(images, "suggest", lambda *a, **k: chamados.append(1) or ([], []))
    monkeypatch.setattr(images, "global_words", lambda *a, **k: [])
    novo = image_plan(project, PipelineOptions(), plano)
    assert chamados == [1] and novo.assinatura != assinatura


def test_sticker_failure_falls_back_to_the_normal_photo(tmp_path, monkeypatch):
    def quebra(origem, img, settings):
        raise RuntimeError("onnxruntime Fail")

    monkeypatch.setattr(images, "_sticker", quebra)
    w, h = prepare_overlay(
        _foto(tmp_path / "f.jpg", 400, 300), (500, 500), tmp_path / "s.png", True
    )
    assert Image.open(tmp_path / "s.png").getpixel((w // 2, 3))[:3] == (255, 255, 255)  # borda
