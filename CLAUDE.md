# Editor de Vídeos

App local que transforma clipes em um vídeo vertical 1080x1920 editado. O plano, as decisões fixas e o checklist ficam em `etapas.md`: é a fonte da verdade. Implemente uma etapa por vez com a skill `etapa-workflow`.

## Comandos
- `uv sync`: instala as dependências (Python 3.12 via `.python-version`). Para a GPU no Whisper: `uv sync --extra gpu`.
- `uv run python -m src.doctor`: verifica FFmpeg, GPU, pacotes e chaves.
- `uv run pytest -q`: testes rápidos; `-m integration` para os lentos (vídeos reais, GPU, rede).
- `uv add <pacote>`: adicionar dependência (nunca edite o `uv.lock`).

## Regras que o código precisa respeitar
- O LLM decide *o quê/quando* (índices de palavras, queries); o código decide *onde/como* (geometria).
- LangChain só em `src/llm/client.py`; prompts em `src/llm/prompts/*.md`.
- Configuração só via `src.config.get_settings()`. O python-dotenv lê `KEY=   # comentário` como valor, e `config._clean` trata isso.
- mediapipe 1.x não tem `mp.solutions`: use `mediapipe.tasks.python.vision.FaceDetector`.

## Automação do Claude Code (.claude/)
- Hooks: bloqueiam edição de `.env`/`uv.lock`/`samples`; formatam `.py` com ruff e bloqueiam LangChain fora do client; rodam o pytest ao fim do turno se algum `.py` mudou; mostram o progresso das etapas ao iniciar a sessão.
- Subagentes: `video-pipeline-engineer`, `llm-integration-engineer`, `ui-engineer`, `test-engineer`, `etapa-verifier` (rode este antes de marcar uma etapa como concluída).
- Skills: `etapa-workflow`, `ffmpeg-video`, `langchain-structured`, `mediapipe-face`.
