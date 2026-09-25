"""Versões do projeto renderizado e navegação por desfazer/refazer."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.cache import atomic_write_text
from src.project import Project


class HistoryEntry(BaseModel):
    version: int
    summary: str
    element_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    diff: dict[str, dict[str, Any]] = Field(default_factory=dict)
    video_sha256: str


class HistoryIndex(BaseModel):
    entries: list[HistoryEntry] = Field(default_factory=list)
    cursor: int = -1
    next_version: int = 0


class HistoryState(BaseModel):
    entries: list[HistoryEntry]
    cursor: int
    can_undo: bool
    can_redo: bool


class RenderedCheckpoint(BaseModel):
    project: Project
    video_sha256: str


def _dir(project_dir: Path) -> Path:
    return project_dir / ".history"


def _path(project_dir: Path, version: int) -> Path:
    return _dir(project_dir) / f"v{version:06d}.json"


def _read(project_dir: Path) -> HistoryIndex:
    path = _dir(project_dir) / "index.json"
    if not path.exists():
        return HistoryIndex()
    return HistoryIndex.model_validate_json(path.read_text(encoding="utf-8"))


def _snapshot(project: Project) -> dict[str, Any]:
    return project.model_dump(mode="json")


def video_sha256(path: Path) -> str:
    """Assinatura integral para exigir que o MP4 restaurado seja idêntico."""
    digest = hashlib.sha256()
    with path.open("rb") as media:
        for chunk in iter(lambda: media.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_rendered_checkpoint(project_dir: Path, project: Project, video: Path) -> None:
    """Registra o documento que realmente corresponde ao MP4 publicado."""
    checkpoint = RenderedCheckpoint(project=project, video_sha256=video_sha256(video))
    atomic_write_text(
        _dir(project_dir) / "rendered.json", checkpoint.model_dump_json(indent=2)
    )


def rendered_matches(project_dir: Path, current: Project, video: Path) -> bool | None:
    """None indica projeto antigo, anterior à criação do checkpoint."""
    path = _dir(project_dir) / "rendered.json"
    if not path.exists():
        return None
    try:
        checkpoint = RenderedCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        _render_snapshot(checkpoint.project) == _render_snapshot(current)
        and video.is_file()
        and checkpoint.video_sha256 == video_sha256(video)
    )


def _render_snapshot(project: Project) -> dict[str, Any]:
    data = _snapshot(project)
    return {key: data[key] for key in ("timeline", "documento")}


def _version(project_dir: Path, version: int) -> Project:
    return Project.model_validate_json(_path(project_dir, version).read_text(encoding="utf-8"))


def _matches(project_dir: Path, index: HistoryIndex, current: Project) -> bool:
    if not 0 <= index.cursor < len(index.entries):
        return False
    try:
        saved = _version(project_dir, index.entries[index.cursor].version)
    except (OSError, ValueError):
        return False
    return _render_snapshot(saved) == _render_snapshot(current)


def history_state(project_dir: Path, current: Project) -> HistoryState:
    """Expõe apenas versões compatíveis com o documento aberto."""
    index = _read(project_dir)
    valid = _matches(project_dir, index, current)
    return HistoryState(
        entries=index.entries if valid else [],
        cursor=index.cursor if valid else -1,
        can_undo=valid and index.cursor > 0,
        can_redo=valid and index.cursor < len(index.entries) - 1,
    )


def _element(project: Project, element_id: str) -> dict[str, Any] | None:
    found = project.documento.por_id(element_id)
    if found is None:
        return None
    if found.tipo == "imagem":
        data = found.dados
        candidate = data.get("candidatos", [])
        chosen = data.get("escolhida", 0)
        selected = (
            candidate[chosen]
            if isinstance(chosen, int) and 0 <= chosen < len(candidate)
            else {}
        )
        return {
            "query": data.get("query"),
            "fonte": selected.get("fonte"),
            "id": selected.get("id"),
        }
    if found.tipo == "broll":
        data = found.dados
        video = data.get("video") or {}
        return {"query": data.get("query"), "fonte": video.get("fonte"), "id": video.get("id")}
    return {"tipo": found.tipo, "inicio": found.inicio, "fim": found.fim, "ativo": found.ativo}


def _diff(before: Project, after: Project, element_ids: list[str]) -> dict[str, dict[str, Any]]:
    return {
        element_id: {"antes": _element(before, element_id), "depois": _element(after, element_id)}
        for element_id in element_ids
    }


def _summary(element_ids: list[str], diff: dict[str, dict[str, Any]]) -> str:
    if len(element_ids) != 1:
        return f"Alteração de {len(element_ids)} elementos"
    element_id = element_ids[0]
    label = "Imagem" if element_id.startswith("img_") else "B-roll"
    before = diff[element_id]["antes"] or {}
    after = diff[element_id]["depois"] or {}
    if before.get("query") != after.get("query"):
        change = f"busca '{before.get('query') or '?'}' → '{after.get('query') or '?'}'"
    elif before.get("fonte") != after.get("fonte"):
        change = f"{before.get('fonte') or '?'} → {after.get('fonte') or '?'}"
    else:
        change = "mídia substituída"
    return f"{label} {element_id}: {change}"


def record_edit(
    project_dir: Path,
    before: Project,
    after: Project,
    element_ids: set[str],
    *,
    max_versions: int,
    before_video_hash: str,
    after_video_hash: str,
) -> HistoryState:
    """Adiciona versão após render bem-sucedido; edição após undo descarta redo."""
    if max_versions < 2:
        raise ValueError("histórico precisa guardar ao menos duas versões")
    if _snapshot(before) == _snapshot(after):
        raise ValueError("a edição não alterou o projeto")
    ids = sorted(element_ids)
    if not ids:
        raise ValueError("informe os IDs alterados para o histórico")
    index = _read(project_dir)
    if not _matches(project_dir, index, before) or (
        index.entries[index.cursor].video_sha256 != before_video_hash
    ):
        baseline = index.next_version
        index.entries = [HistoryEntry(
            version=baseline, summary="Vídeo antes da edição",
            created_at=datetime.now(UTC), video_sha256=before_video_hash,
        )]
        index.cursor = 0
        index.next_version += 1
        atomic_write_text(_path(project_dir, baseline), before.model_dump_json(indent=2))
    index.entries = index.entries[: index.cursor + 1]
    version = index.next_version
    index.next_version += 1
    diff = _diff(before, after, ids)
    index.entries.append(HistoryEntry(
        version=version, summary=_summary(ids, diff), element_ids=ids,
        created_at=datetime.now(UTC), diff=diff, video_sha256=after_video_hash,
    ))
    index.cursor = len(index.entries) - 1
    atomic_write_text(_path(project_dir, version), after.model_dump_json(indent=2))
    if len(index.entries) > max_versions:
        removed = len(index.entries) - max_versions
        index.entries = index.entries[removed:]
        index.cursor -= removed
    atomic_write_text(
        _dir(project_dir) / "index.json",
        json.dumps(index.model_dump(mode="json"), ensure_ascii=False, indent=2),
    )
    kept = {entry.version for entry in index.entries}
    for path in _dir(project_dir).glob("v*.json"):
        if not path.stem[1:].isdigit() or int(path.stem[1:]) not in kept:
            path.unlink()
    return history_state(project_dir, after)


def navigation_target(
    project_dir: Path, current: Project, direction: Literal["undo", "redo"],
    current_video_hash: str | None = None,
) -> tuple[Project, set[str], int, str]:
    """Valida cursor e carrega a versão de destino sem alterar o disco."""
    index = _read(project_dir)
    if not _matches(project_dir, index, current):
        raise ValueError("histórico não corresponde ao projeto atual; gere o vídeo novamente")
    if current_video_hash is not None and (
        index.entries[index.cursor].video_sha256 != current_video_hash
    ):
        raise ValueError("o vídeo atual difere da versão salva no histórico")
    target_cursor = index.cursor + (-1 if direction == "undo" else 1)
    if not 0 <= target_cursor < len(index.entries):
        raise ValueError("não há mais edições para desfazer ou refazer")
    changed = index.entries[index.cursor if direction == "undo" else target_cursor].element_ids
    target = _version(project_dir, index.entries[target_cursor].version)
    # Opções salvas no painel podem ter mudado sem render (inclusive num job
    # cancelado). Elas não pertencem ao histórico do vídeo; preserve as atuais.
    for field in (
        "estabilizar", "suavizacao_estabilizacao", "broll", "broll_intervalo_min",
        "broll_transition", "legendas_continuas", "legendas_destaque", "estilo_destaque",
    ):
        setattr(target, field, getattr(current, field))
    return target, set(changed), target_cursor, index.entries[target_cursor].video_sha256


def set_cursor(project_dir: Path, expected: int, target: int) -> None:
    index = _read(project_dir)
    if index.cursor != expected or abs(target - expected) != 1:
        raise ValueError("histórico foi alterado durante o render")
    index.cursor = target
    atomic_write_text(
        _dir(project_dir) / "index.json",
        json.dumps(index.model_dump(mode="json"), ensure_ascii=False, indent=2),
    )


def clear_history(project_dir: Path) -> None:
    """Nova geração integral inicia outra linha de versões."""
    folder = _dir(project_dir)
    if not folder.exists():
        return
    for path in folder.iterdir():
        if path.name in {"index.json", "rendered.json"} or (
            path.name.startswith("v") and path.suffix == ".json"
        ):
            path.unlink()
    try:
        folder.rmdir()
    except OSError:
        # Arquivos desconhecidos não devem transformar um render concluído em erro.
        pass
