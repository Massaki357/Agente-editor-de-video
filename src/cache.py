"""Hash rápido de arquivo e cache em disco (JSON) por tipo e chave.

Layout: `CACHE_DIR/<tipo>/<chave>.json`, ex.: `.cache/transcricao/<hash>.json`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.config import get_settings

log = logging.getLogger(__name__)

HASH_CHUNK = 1024 * 1024  # 1 MB do começo e 1 MB do fim


def file_hash(path: str | Path) -> str:
    """SHA-256 de (tamanho + primeiro MB + último MB). Rápido mesmo para vídeos grandes.

    Memoizado por (caminho, tamanho, mtime): se o arquivo muda, o hash é recalculado.
    """
    p = Path(path).resolve()
    st = p.stat()
    return _file_hash(str(p), st.st_size, st.st_mtime_ns)


@lru_cache(maxsize=1024)
def _file_hash(path: str, size: int, _mtime_ns: int) -> str:
    h = hashlib.sha256()
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(HASH_CHUNK))
        if size > HASH_CHUNK:
            f.seek(max(size - HASH_CHUNK, HASH_CHUNK))
            h.update(f.read(HASH_CHUNK))
    return h.hexdigest()


def cache_path(kind: str, key: str, suffix: str = ".json", cache_dir: Path | None = None) -> Path:
    base = cache_dir or get_settings().cache_dir
    return base / kind / f"{key}{suffix}"


def read_json_cache(kind: str, key: str, cache_dir: Path | None = None) -> Any | None:
    """Devolve o conteúdo do cache ou `None` se não existe ou está corrompido."""
    path = cache_path(kind, key, cache_dir=cache_dir)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Cache corrompido ignorado (%s): %s", path, exc)
        return None


def write_json_cache(kind: str, key: str, data: Any, cache_dir: Path | None = None) -> Path:
    """Grava de forma atômica (arquivo temporário + rename) para não deixar cache pela metade."""
    path = cache_path(kind, key, cache_dir=cache_dir)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False))
    return path


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
