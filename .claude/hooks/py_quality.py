"""PostToolUse (Edit/Write): formata .py com ruff e aplica as regras de arquitetura do etapas.md.

- Só `src/llm/client.py` pode importar LangChain.
- Marca que há código alterado para o hook Stop rodar o pytest.
"""

import json
import os
import re
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):  # console do Windows não é UTF-8 por padrão
    _s.reconfigure(encoding="utf-8")
from pathlib import Path

data = json.load(sys.stdin)
file_path = (data.get("tool_input") or {}).get("file_path") or ""
if not file_path.endswith(".py"):
    sys.exit(0)

root = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
path = Path(file_path).resolve()
try:
    rel = path.relative_to(root).as_posix()
except ValueError:
    sys.exit(0)  # fora do projeto
if rel.startswith(".claude/") or not path.exists():
    sys.exit(0)

(root / ".claude" / ".tests_dirty").touch()

for cmd in (["ruff", "check", "--fix", "--quiet", str(path)], ["ruff", "format", "--quiet", str(path)]):
    subprocess.run(["uv", "run", "--quiet", *cmd], cwd=root, capture_output=True)

problems = []
source = path.read_text(encoding="utf-8", errors="replace")

if rel.startswith("src/") and rel != "src/llm/client.py":
    if re.search(r"^\s*(from|import)\s+langchain", source, re.MULTILINE):
        problems.append(
            f"{rel} importa LangChain. Regra fixa: todo acesso ao LLM passa por "
            "src/llm/client.py (run_structured). Mova o uso para lá."
        )

lint = subprocess.run(
    ["uv", "run", "--quiet", "ruff", "check", "--quiet", str(path)],
    cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
)
if lint.returncode != 0 and lint.stdout.strip():
    problems.append("ruff encontrou problemas que não corrige sozinho:\n" + lint.stdout.strip()[:2000])

if problems:
    print("\n\n".join(problems), file=sys.stderr)
    sys.exit(2)
