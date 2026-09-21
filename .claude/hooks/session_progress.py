"""SessionStart: injeta no contexto o progresso do checklist do etapas.md."""

import os
import re
from pathlib import Path
import sys

for _s in (sys.stdout, sys.stderr):  # console do Windows não é UTF-8 por padrão
    _s.reconfigure(encoding="utf-8")

root = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
etapas = root / "etapas.md"
if etapas.exists():
    text = etapas.read_text(encoding="utf-8")
    items = re.findall(r"^- \[( |x)\] (Etapa \d+b?: .+)$", text, re.MULTILINE)
    done = [name for mark, name in items if mark == "x"]
    todo = [name for mark, name in items if mark == " "]
    print(f"Progresso do etapas.md: {len(done)}/{len(items)} etapas concluídas.")
    if todo:
        print(f"Próxima etapa: {todo[0]}. Use a skill `etapa-workflow` para implementá-la.")
