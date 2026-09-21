"""Stop: se algum .py mudou nesta sessão, roda o pytest (sem testes de integração).

Se falhar, devolve o erro para o Claude corrigir antes de encerrar o turno.
"""

import json
import os
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):  # console do Windows não é UTF-8 por padrão
    _s.reconfigure(encoding="utf-8")
from pathlib import Path

data = json.load(sys.stdin)
root = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
marker = root / ".claude" / ".tests_dirty"

if not marker.exists() or data.get("stop_hook_active"):
    sys.exit(0)

result = subprocess.run(
    ["uv", "run", "--quiet", "pytest", "-q", "-x", "-m", "not integration", "--no-header"],
    cwd=root / "API", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
)
# 5 = nenhum teste coletado
if result.returncode in (0, 5):
    marker.unlink(missing_ok=True)
    sys.exit(0)

tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-40:])
print(f"pytest falhou depois das alterações. Corrija antes de encerrar:\n{tail}", file=sys.stderr)
sys.exit(2)
