"""PreToolUse (Edit/Write): bloqueia edição de arquivos que o agente não deve tocar."""

import json
import sys

for _s in (sys.stdout, sys.stderr):  # console do Windows não é UTF-8 por padrão
    _s.reconfigure(encoding="utf-8")
from pathlib import PurePath

data = json.load(sys.stdin)
path = (data.get("tool_input") or {}).get("file_path") or ""
p = PurePath(path.replace("\\", "/"))
parts = [s.lower() for s in p.parts]

reason = None
if p.name == ".env":
    reason = ".env contém segredos do usuário; edite só o .env.example."
elif p.name == "uv.lock":
    reason = "uv.lock é gerado; altere o pyproject.toml e rode `uv sync`/`uv add`."
elif "samples" in parts and p.suffix.lower() in {".mp4", ".mov", ".mkv", ".wav", ".mp3"}:
    reason = "samples/ guarda vídeos de teste do usuário; não sobrescreva."
elif ".cache" in parts:
    reason = ".cache/ é gerenciado por src/cache.py; não edite à mão."

if reason:
    print(f"Bloqueado pelo hook protect_files: {reason}", file=sys.stderr)
    sys.exit(2)
