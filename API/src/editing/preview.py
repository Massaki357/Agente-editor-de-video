"""Edições propostas e prévias curtas para a interface pós-render."""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.cache import atomic_write_text
from src.images import PlanoImagens
from src.project import Project

Action = Literal["remove", "replace", "chat"]
TOKEN = re.compile(r"^[0-9a-f]{32}$")
EDITABLE_TYPES = {"imagem", "broll", "zoom", "destaque"}


class EditorElement(BaseModel):
    id: str
    tipo: Literal["imagem", "broll", "zoom", "destaque"]
    inicio: float
    fim: float
    rotulo: str
    ativo: bool
    editavel: bool


class EditorOut(BaseModel):
    duration: float
    elements: list[EditorElement]


class PreviewProposal(BaseModel):
    token: str
    element_id: str
    action: Action
    base_sha256: str
    base_document_sha256: str
    plan: PlanoImagens
    render_options: dict[str, Any]
    inicio: float
    fim: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def list_editor_elements(project: Project, options: dict[str, Any]) -> EditorOut:
    """Lista decisões visuais da versão renderizada com IDs e tempos finais."""
    enabled = {
        "imagem": bool(options.get("imagens")),
        "broll": bool(options.get("broll") and options.get("reenquadrar")),
        "zoom": bool(options.get("zooms") and options.get("reenquadrar")),
        "destaque": bool(options.get("legendas_destaque")),
    }
    elements = []
    for item in project.documento.elementos:
        if item.tipo not in EDITABLE_TYPES or item.obsoleto:
            continue
        data = item.dados
        label = {
            "imagem": data.get("palavra") or data.get("query") or "Imagem",
            "broll": data.get("texto") or data.get("query") or "B-roll",
            "zoom": data.get("palavra") or "Zoom",
            "destaque": data.get("texto") or "Destaque",
        }[item.tipo]
        active = item.ativo
        if item.tipo == "broll":
            active = active and bool(data.get("aprovado") and data.get("video"))
        elements.append(EditorElement(
            id=item.id, tipo=item.tipo, inicio=item.inicio, fim=item.fim,
            rotulo=str(label), ativo=active, editavel=enabled[item.tipo],
        ))
    elements.sort(key=lambda item: (item.inicio, item.fim, item.id))
    return EditorOut(duration=project.timeline.duracao_total, elements=elements)


def element_bounds(plan: PlanoImagens, element_id: str) -> tuple[float, float]:
    """Intervalo do efeito, incluindo a permanência da legenda de destaque."""
    if element_id.startswith("img_"):
        items = plan.itens
    elif element_id.startswith("broll_"):
        items = plan.broll
    elif element_id.startswith("zoom_"):
        items = plan.zooms
    elif element_id.startswith("highlight_"):
        items = plan.destaques
    else:
        raise KeyError(element_id)
    prefix = element_id.rsplit("_", 1)[0]
    item = next((item for item in items if f"{prefix}_{item.id:03d}" == element_id), None)
    if item is None:
        raise KeyError(element_id)
    end = item.fim
    if element_id.startswith("highlight_"):
        duration = item.duracao_permanencia
        if duration is None:
            duration = plan.duracao_permanencia_destaques
        end += duration if duration is not None else 1.2
    return item.inicio, end


def remove_element(plan: PlanoImagens, element_id: str) -> PlanoImagens:
    """Desativa por ID; conserva o item para desfazer e para IDs estáveis."""
    updated = plan.model_copy(deep=True)
    if element_id.startswith("img_"):
        items, flag = updated.itens, "ativa"
    elif element_id.startswith("broll_"):
        items, flag = updated.broll, "ativo"
    elif element_id.startswith("zoom_"):
        items, flag = updated.zooms, "ativo"
    elif element_id.startswith("highlight_"):
        items, flag = updated.destaques, "ativo"
    else:
        raise KeyError(element_id)
    prefix = element_id.rsplit("_", 1)[0]
    item = next((item for item in items if f"{prefix}_{item.id:03d}" == element_id), None)
    if item is None:
        raise KeyError(element_id)
    if not getattr(item, flag):
        raise ValueError("elemento já está removido")
    setattr(item, flag, False)
    return updated


def preview_window(plan: PlanoImagens, element_id: str, total: float) -> tuple[float, float]:
    start, end = element_bounds(plan, element_id)
    return max(0.0, start - 0.6), min(total, end + 0.6)


def limit_plan(plan: PlanoImagens, window: tuple[float, float]) -> PlanoImagens:
    """Evita preparar mídia de trechos que não entram na prévia."""
    selected = plan.model_copy(deep=True)
    start, end = window
    for prefix, items, flag in (
        ("img", selected.itens, "ativa"), ("broll", selected.broll, "ativo"),
        ("zoom", selected.zooms, "ativo"),
        ("highlight", selected.destaques, "ativo"),
    ):
        for item in items:
            item_start, item_end = element_bounds(selected, f"{prefix}_{item.id:03d}")
            if item_end <= start or item_start >= end:
                setattr(item, flag, False)
    return selected


def preview_dir(project_dir: Path, token: str) -> Path:
    if not TOKEN.fullmatch(token):
        raise ValueError("token de prévia inválido")
    return project_dir / ".editor_previews" / token


def document_sha256(project: Project) -> str:
    return hashlib.sha256(project.documento.model_dump_json().encode()).hexdigest()


def save_proposal(project_dir: Path, proposal: PreviewProposal) -> Path:
    path = preview_dir(project_dir, proposal.token) / "proposal.json"
    atomic_write_text(path, proposal.model_dump_json(indent=2))
    return path


def load_proposal(project_dir: Path, token: str) -> PreviewProposal:
    path = preview_dir(project_dir, token) / "proposal.json"
    return PreviewProposal.model_validate_json(path.read_text(encoding="utf-8"))


def clean_previews(project_dir: Path, keep: int = 3) -> None:
    root = project_dir / ".editor_previews"
    if not root.exists():
        return
    folders = sorted(
        (path for path in root.iterdir() if path.is_dir() and TOKEN.fullmatch(path.name)),
        key=lambda path: path.stat().st_mtime_ns, reverse=True,
    )
    for path in folders[keep:]:
        shutil.rmtree(path)
