"""Migra projetos antigos para o project.json v2, com backup da versão 1."""

from __future__ import annotations

import logging
from pathlib import Path

from src.api.store import ProjectStore
from src.config import get_settings

log = logging.getLogger(__name__)


def migrate_projects(root: Path | None = None) -> tuple[int, int]:
    """Retorna quantos projetos foram examinados e quantos falharam."""
    store = ProjectStore(root or get_settings().data_dir / "projects")
    total, errors = 0, 0
    for path in sorted(store.root.glob("*/project.json")):
        total += 1
        try:
            store.load_plan(path.parent.name)
            store.save(path.parent.name, store.load(path.parent.name))
        except Exception:
            errors += 1
            log.exception("Falha ao migrar %s", path)
    return total, errors


def main() -> int:
    from src.logging_setup import setup_logging

    setup_logging()
    total, errors = migrate_projects()
    print(f"Projetos examinados: {total}; falhas: {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
