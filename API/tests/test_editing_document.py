"""Parte 5, Etapa 0: project.json como documento versionado e fonte do plano."""

import json

import numpy as np
import pytest
from pydantic import ValidationError

from src.api.store import ProjectStore
from src.broll.planner import ItemBroll
from src.editing.project_schema import (
    DocumentoEdicao,
    Elemento,
    com_crops,
    com_plano,
    corrigir_intervalo_destaques,
    eventos_ass,
    keyframes_crop,
    plano_do_documento,
)
from src.highlight_captions.planner import ItemDestaque
from src.images import ItemImagem, ItemZoom, PlanoImagens, save_plan
from src.project import Clip, Project, Timeline
from src.reframe import CameraPath


def _plano() -> PlanoImagens:
    return PlanoImagens(
        assinatura="trechos-estaveis",
        itens=[
            ItemImagem(
                id=3,
                indice=2,
                clipe=0,
                palavra="café",
                query="coffee",
                inicio=0.5,
                duracao=1.5,
            )
        ],
        zooms=[ItemZoom(id=2, indice=4, clipe=0, palavra="forte", inicio=1, duracao=1)],
        broll=[
            ItemBroll(
                id=1,
                clipe=0,
                trecho_inicio_palavra=0,
                trecho_fim_palavra=4,
                texto="este café é muito bom",
                query="coffee roasting",
                inicio=0,
                duracao_max=2,
                motivo="ilustra a fala",
            )
        ],
        destaques=[
            ItemDestaque(
                id=5,
                clipe=0,
                segmento=0,
                trecho_inicio_palavra=0,
                trecho_fim_palavra=2,
                inicio=0.2,
                fim=1.2,
                texto="café muito bom",
                motivo="impacto",
            )
        ],
        versao_destaques=2,
        duracao_permanencia_destaques=1.2,
    )


def test_new_project_keeps_unique_stable_ids_and_plan_in_project_json(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    pid = store.create("teste").id
    project = store.load(pid)
    project.timeline = Timeline(clipes=[Clip(arquivo="camera.mp4", trechos=[(0, 3)])])
    store.save(pid, project)
    store.save_plan(pid, _plano())

    path = store.dir(pid) / "project.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    elements = raw["documento"]["elementos"]
    ids = {e["id"] for e in elements}
    assert raw["versao"] == raw["documento"]["versao"] == 2
    assert {"img_003", "zoom_002", "broll_001", "highlight_005"} <= ids
    assert any(id.startswith("cut_") for id in ids)
    assert any(id.startswith("clip_") for id in ids)
    assert len(elements) == len(ids)
    assert not store.plan_path(pid).exists()  # projetos novos não criam outra fonte de verdade

    again = store.load(pid)
    assert {e.id for e in again.documento.elementos} == ids
    assert store.load_plan(pid) == _plano()


def test_old_project_and_sidecar_migrate_with_backup_and_without_loss(tmp_path):
    folder = tmp_path / "legacy"
    folder.mkdir()
    antigo = {
        "versao": 1,
        "timeline": {
            "clipes": [{"arquivo": "camera.mp4", "trechos": [[0, 3]]}],
            "imagens": [{"inicio": 1, "duracao": 1.2, "query": "coffee"}],
            "zooms": [{"inicio": 1.5, "duracao": 1}],
            "legendas": "legendas.ass",
        },
    }
    path = folder / "project.json"
    path.write_text(json.dumps(antigo), encoding="utf-8")
    save_plan(_plano(), folder / "plano_imagens.json")

    migrated = Project.carregar(path)

    assert migrated.versao == 2
    assert migrated.timeline.imagens[0].query == "coffee"
    assert migrated.timeline.legendas == "legendas.ass"
    assert any(e.id.startswith("img_legacy_") for e in migrated.documento.elementos)
    assert any(e.id.startswith("zoom_legacy_") for e in migrated.documento.elementos)
    assert any(e.id.startswith("caption_legacy_") for e in migrated.documento.elementos)
    assert plano_do_documento(migrated.documento) == _plano()
    assert json.loads((folder / "project.v1.json").read_text(encoding="utf-8")) == antigo
    assert Project.carregar(path).timeline.clipes[0].id == migrated.timeline.clipes[0].id
    assert len([e for e in migrated.documento.elementos if e.tipo == "corte"]) == 1


def test_early_v2_document_without_origin_fields_is_upgraded(tmp_path):
    path = tmp_path / "project.json"
    Project(timeline=Timeline(clipes=[Clip(arquivo="camera.mp4", trechos=[(0, 1)])])).salvar(
        path
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["documento"].pop("assinatura_timeline")
    for element in raw["documento"]["elementos"]:
        element.pop("origem")
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = Project.carregar(path)
    loaded.salvar(path)
    assert [e.origem for e in Project.carregar(path).documento.elementos] == [
        "timeline", "timeline"
    ]


def test_document_rejects_duplicate_ids_and_invalid_intervals():
    element = Elemento(id="img_001", tipo="imagem", inicio=0, fim=1)
    with pytest.raises(ValidationError, match="duplicados"):
        DocumentoEdicao(elementos=[element, element])
    with pytest.raises(ValidationError, match="fim precisa"):
        Elemento(id="zoom_001", tipo="zoom", inicio=2, fim=1)


def test_element_fields_drive_plan_and_stale_sidecar_cannot_restore_it(tmp_path):
    doc = com_plano(DocumentoEdicao(), _plano())
    image = doc.por_id("img_003")
    image.inicio = 0.75
    image.fim = 2.5
    image.ativo = False
    restored = plano_do_documento(doc)
    assert restored.itens[0].inicio == 0.75
    assert restored.itens[0].duracao == 1.75
    assert restored.itens[0].ativa is False
    highlight = doc.por_id("highlight_005")
    assert highlight.fim == pytest.approx(2.4)  # fim visual, incluindo a permanência
    highlight.fim = 2.2
    assert plano_do_documento(doc).destaques[0].duracao_permanencia == pytest.approx(1.0)
    highlight.fim = highlight.dados["fim"]  # formato v2 inicial, sem a cauda visual
    corrigir_intervalo_destaques(doc)
    assert highlight.fim == pytest.approx(2.4)

    store = ProjectStore(tmp_path / "projects")
    pid = store.create("novo").id
    save_plan(_plano(), store.plan_path(pid))  # sidecar colocado após criar documento v2
    assert store.load_plan(pid) is None


def test_changing_cuts_marks_previous_visual_elements_obsolete(tmp_path):
    path = tmp_path / "project.json"
    project = Project(timeline=Timeline(clipes=[Clip(arquivo="camera.mp4", trechos=[(0, 3)])]))
    project.salvar(path)
    project.documento = com_plano(project.documento, _plano())
    project.documento = com_crops(
        project.documento,
        [Elemento(id="crop_test_001", tipo="crop", inicio=0, fim=1, origem="render")],
    )
    project.salvar(path)

    project.timeline.substituir_trechos(0, [(0, 1)])
    project.salvar(path)
    loaded = Project.carregar(path)
    assert loaded.documento.por_id("img_003").obsoleto is True
    assert loaded.documento.por_id("crop_test_001").obsoleto is True
    assert len([e for e in loaded.documento.elementos if e.tipo == "corte"]) == 1
    assert loaded.documento.por_id("img_003").fim > loaded.timeline.duracao_total


def test_crop_keyframes_and_ass_events_are_traceable(tmp_path):
    timeline = Timeline(clipes=[Clip(arquivo="camera.mp4", trechos=[(0.5, 1.5)])])
    timeline.recalcular_offsets()
    camera = CameraPath(
        largura=200,
        altura=100,
        fps=2,
        cw=50,
        ch=100,
        x=np.array([0.0, 5.0, 10.0, 15.0]),
        y=np.zeros(4),
    )
    crops = keyframes_crop(timeline, {0: camera})
    assert len(crops) == 2
    assert crops[0].inicio == 0 and crops[-1].fim == pytest.approx(1)
    assert crops[0].dados["inicio_src"] == 0.5
    assert len({e.id for e in crops}) == 2
    assert len(com_crops(DocumentoEdicao(), crops).elementos) == 2

    ass = tmp_path / "legendas.ass"
    ass.write_text(
        "Dialogue: 0,0:00:00.50,0:00:01.00,Default,,0,0,0,,{\\c&H00FFFF&}Olá\\N mundo\n",
        encoding="utf-8",
    )
    captions = eventos_ass(ass)
    assert len(captions) == 1
    assert captions[0].id.startswith("caption_")
    assert (captions[0].inicio, captions[0].fim, captions[0].dados["texto"]) == (
        0.5, 1.0, "Olá  mundo"
    )
    ass.write_text(
        "Dialogue: 0,0:00:00.10,0:00:00.20,Default,,0,0,0,,Antes\n"
        + ass.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert eventos_ass(ass)[1].id == captions[0].id
