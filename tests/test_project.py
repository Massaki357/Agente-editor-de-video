import json

import pytest
from pydantic import ValidationError

from src.project import Clip, ClipMeta, Imagem, Project, Timeline, Zoom


def _timeline() -> Timeline:
    # Exemplo do etapas.md: clipe 1 mantém 7,8 s + 5,9 s = 13,7 s.
    t = Timeline(
        clipes=[
            Clip(arquivo="1.mp4", trechos=[(0.4, 8.2), (9.1, 15.0)]),
            Clip(arquivo="2.mp4", trechos=[(0.2, 12.5)]),
            Clip(arquivo="3.mp4", trechos=[(1.0, 3.0)]),
        ]
    )
    t.recalcular_offsets()
    return t


def offsets(t: Timeline) -> list[float]:
    return [c.offset for c in t.clipes]


def names(t: Timeline) -> list[str]:
    return [c.arquivo for c in t.clipes]


def test_offsets_match_etapas_example():
    t = _timeline()
    assert offsets(t) == pytest.approx([0.0, 13.7, 26.0])
    assert t.duracao_total == pytest.approx(28.0)


def test_move_clip_reorders_and_recomputes_offsets():
    t = _timeline()
    t.mover_clipe(2, 0)  # 3.mp4 (2 s) vai para o começo
    assert names(t) == ["3.mp4", "1.mp4", "2.mp4"]
    assert offsets(t) == pytest.approx([0.0, 2.0, 15.7])


def test_reorder_by_indices():
    t = _timeline()
    t.reordenar([1, 2, 0])
    assert names(t) == ["2.mp4", "3.mp4", "1.mp4"]
    assert offsets(t) == pytest.approx([0.0, 12.3, 14.3])


@pytest.mark.parametrize("ordem", [[0, 1], [0, 0, 1], [0, 1, 3]])
def test_reorder_rejects_invalid_order(ordem):
    with pytest.raises(ValueError):
        _timeline().reordenar(ordem)


def test_remove_clip_recomputes_offsets():
    t = _timeline()
    removed = t.remover_clipe(0)
    assert removed.arquivo == "1.mp4"
    assert names(t) == ["2.mp4", "3.mp4"]
    assert offsets(t) == pytest.approx([0.0, 12.3])


def test_add_clip_goes_to_end():
    t = _timeline()
    t.adicionar_clipe(Clip(arquivo="4.mp4", trechos=[(0.0, 1.0)]))
    assert offsets(t)[-1] == pytest.approx(28.0)


def test_changing_order_discards_plan_in_output_time():
    t = _timeline()
    t.imagens.append(Imagem(inicio=3.4, duracao=2.0, query="fruit basket"))
    t.zooms.append(Zoom(inicio=5.0, duracao=1.5))
    t.legendas = "legendas.ass"
    t.mover_clipe(0, 1)
    assert (t.imagens, t.zooms, t.legendas) == ([], [], None)


@pytest.mark.parametrize(
    "trechos",
    [[(2.0, 1.0)], [(-0.1, 1.0)], [(0.0, 2.0), (1.5, 3.0)], [(3.0, 4.0), (0.0, 1.0)]],
)
def test_invalid_segments_are_rejected(trechos):
    with pytest.raises(ValidationError):
        Clip(arquivo="x.mp4", trechos=trechos)


def test_segment_beyond_clip_duration_is_rejected():
    meta = ClipMeta(duracao=5.0, largura=1920, altura=1080, fps=30, tem_audio=True)
    with pytest.raises(ValidationError):
        Clip(arquivo="x.mp4", trechos=[(0.0, 6.0)], meta=meta)


def test_save_and_load_roundtrip_with_relative_paths(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    project = Project(timeline=_timeline())
    for clip in project.timeline.clipes:
        clip.arquivo = str(folder / clip.arquivo)
    project.timeline.imagens.append(Imagem(inicio=3.4, duracao=2.0, query="fruit basket"))

    path = project.salvar(folder / "project.json")

    raw = json.loads(path.read_text(encoding="utf-8"))
    tl = raw["timeline"]
    assert set(tl) == {"clipes", "imagens", "zooms", "legendas"}
    assert tl["clipes"][0]["arquivo"] == "1.mp4"
    assert tl["clipes"][0]["trechos"] == [[0.4, 8.2], [9.1, 15.0]]
    assert tl["clipes"][1]["offset"] == pytest.approx(13.7)
    assert tl["imagens"] == [{"inicio": 3.4, "duracao": 2.0, "query": "fruit basket"}]

    loaded = Project.carregar(path)
    assert names(loaded.timeline) == [
        str(folder.resolve() / n) for n in ("1.mp4", "2.mp4", "3.mp4")
    ]
    assert offsets(loaded.timeline) == pytest.approx([0.0, 13.7, 26.0])
    assert loaded.timeline.imagens == project.timeline.imagens


def test_save_keeps_absolute_path_outside_project_folder(tmp_path):
    outside = tmp_path / "outros" / "a.mp4"
    project = Project(timeline=Timeline(clipes=[Clip(arquivo=str(outside), trechos=[(0, 1)])]))
    path = project.salvar(tmp_path / "proj" / "project.json")
    saved = json.loads(path.read_text(encoding="utf-8"))["timeline"]["clipes"][0]["arquivo"]
    assert saved == str(outside)
    assert project.timeline.clipes[0].arquivo == str(outside)  # o original não é alterado


@pytest.mark.parametrize("valor", [float("nan"), float("inf")])
def test_nan_and_inf_segments_are_rejected(valor):
    with pytest.raises(ValidationError):
        Clip(arquivo="x.mp4", trechos=[(0.0, valor)])
    with pytest.raises(ValidationError):
        Clip.model_validate_json('{"arquivo": "x", "trechos": [[0, NaN]]}')


@pytest.mark.parametrize(("de", "para"), [(0, 3), (-1, 0), (3, 0)])
def test_move_clip_rejects_out_of_range(de, para):
    t = _timeline()
    with pytest.raises(IndexError):
        t.mover_clipe(de, para)
    assert names(t) == ["1.mp4", "2.mp4", "3.mp4"]
