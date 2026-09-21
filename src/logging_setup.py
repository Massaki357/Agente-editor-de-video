"""Logging padrão: console + arquivo rotativo em `LOG_DIR/editor.log`."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_configured = False


def setup_logging(log_dir: Path | None = None, level: str | None = None) -> logging.Logger:
    """Configura o logger raiz uma única vez. Chamadas seguintes são no-op."""
    global _configured
    root = logging.getLogger()
    if _configured:
        return root

    if log_dir is None or level is None:
        from src.config import get_settings

        settings = get_settings()
        log_dir = log_dir or settings.log_dir
        level = level or settings.log_level

    level = level.upper()
    valid_level = level in logging.getLevelNamesMapping()
    root.setLevel(level if valid_level else logging.INFO)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    root.addHandler(console)

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "editor.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(file_handler)
    except OSError as exc:  # sem permissão de escrita não deve impedir o app de rodar
        root.warning("Não foi possível criar o log em arquivo em %s: %s", log_dir, exc)

    # Bibliotecas que logam cada requisição HTTP (download de modelos, APIs).
    for noisy in ("httpx", "httpcore", "huggingface_hub", "urllib3", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if not valid_level:
        root.warning("LOG_LEVEL inválido: %r; usando INFO.", level)

    _configured = True
    return root
