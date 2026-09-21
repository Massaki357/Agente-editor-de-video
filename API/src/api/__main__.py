"""`python -m src.api [--port 8000] [--reload]` sobe a API com uvicorn."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m src.api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="recarrega ao editar o código")
    args = parser.parse_args()
    uvicorn.run("src.api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
