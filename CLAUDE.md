# Editor de Vídeos

App local que transforma clipes em um vídeo vertical 1080x1920 editado. O sistema tem duas partes: uma **API FastAPI** em `API/`, com todo o processamento, e um **frontend web** em `frontend/`, que só conversa com a API.

O plano, as decisões fixas e o checklist ficam em `etapas.md`, que é a fonte da verdade. Implemente uma etapa por vez com a skill `etapa-workflow`. Testes que dependem de vídeos do usuário ou de checagem manual ficam em `testes-pendentes.md`.

## Layout
- `API/`: backend Python 3.12 com `uv`: `src/` (núcleo + `src/api/`), `tests/`, `pyproject.toml`, `.cache/` e `data/` (projetos da API). **Todo comando `uv` roda dentro de `API/`.**
- `frontend/`: Vite + React + TypeScript. Todas as chamadas passam por `frontend/src/api.ts`; em dev, o Vite faz proxy de `/api` para `http://127.0.0.1:8000`.
- `samples/` (vídeos de teste do usuário) e `output/` (resultados para conferir) ficam na raiz.
- `.env` na raiz ou em `API/.env`: `config.load_settings` lê os dois, e `API/.env` tem precedência.

## Comandos
- Backend (dentro de `API/`):
  - `uv sync`: instala as dependências. Para usar a GPU no Whisper sem CUDA instalado: `uv sync --extra gpu`.
  - `uv run python -m src.api [--reload]`: sobe a API em http://127.0.0.1:8000 (documentação em `/docs`).
  - `uv run python -m src.doctor`: verifica FFmpeg, GPU, pacotes, chaves e modelo de rosto.
  - `uv run pytest -q`: testes rápidos. Com `-m integration`, roda os lentos (vídeos reais em `../samples`, GPU, LLM pago).
  - CLIs do núcleo:
    - `python -m src.pipeline`: pipeline completo pela linha de comando;
    - `python -m src.transcribe`: transcrição de um clipe;
    - `python -m src.face clipe.mp4 -o debug.mp4`: rastreio de rosto com vídeo de debug;
    - `python -m src.render`: render de um `project.json`.
  - `uv add <pacote>`: adicionar dependência. Nunca edite o `uv.lock` à mão.
- Frontend (dentro de `frontend/`): `npm install`, `npm run dev` (http://localhost:5173, com a API rodando) e `npm run build`.

## Regras que o código precisa respeitar
- O LLM decide *o quê/quando* (índices de palavras, queries); o código decide *onde/como* (geometria).
- LangChain só em `API/src/llm/client.py`; prompts em `API/src/llm/prompts/*.md`.
- Configuração só via `src.config.get_settings()`. O python-dotenv lê `KEY=   # comentário` como valor, e `config._clean` trata isso.
- Processamento pesado na API é sempre um **job** (`API/src/api/tasks.py`), numa fila com um worker. Um projeto com job ativo não pode ser alterado (a API responde 409). Toda funcionalidade nova entra no núcleo (`src/`), é exposta na API (com testes em `tests/test_api.py`) e ganha um controle mínimo no frontend.
- Legendas: `.ass` gerado por `src/captions.py` a partir das palavras em t_out, **por clipe** (nunca atravessa emenda), e queimado na passada 2 do render (`_second_pass`, filtro `ass=`). A fonte (Poppins Bold, OFL) fica em `API/fonts/` e é copiada para a pasta de trabalho do render.
- Imagens (`src/images.py`): o LLM só escolhe as palavras e a query; o código faz a validação (densidade, 1,2–3 s, sem atravessar emenda), a busca (Pexels, com o Pixabay como alternativa, e cache) e o posicionamento (zonas livres de rosto e legenda). O plano fica em `API/data/projects/<id>/plano_imagens.json`: o job `imagens` é a prévia, e o `gerar` reaproveita o plano se a `assinatura` (trechos) não mudou.
- Trechos (`Clip.trechos`) ficam na grade de frames (1/30 s): `cuts.TimeMap` e o render produzem exatamente os mesmos tempos.
- Rosto:
  - O mediapipe 1.x não tem `mp.solutions`: use `mediapipe.tasks.python.vision.FaceDetector`.
  - O BlazeFace não enxerga rostos de tamanho normal num quadro 16:9 inteiro; `face.square_crops` resolve isso detectando em recortes quadrados.
  - `FaceTrack.cx/cy/w/h` é o caminho suave da câmera (Etapas 6 e 9); `FaceTrack.box_at(t)` é a caixa real do rosto (Etapas 8 e 9).

## Automação do Claude Code (.claude/)
- **Hooks:**
  - bloqueiam edição de `.env`, `uv.lock`, vídeos em `samples/` e `.cache/`;
  - formatam `.py` de `API/` com ruff e bloqueiam LangChain fora do client;
  - rodam o pytest (em `API/`) ao fim do turno se algum `.py` mudou;
  - mostram o progresso das etapas ao iniciar a sessão.
- **Subagentes:** `video-pipeline-engineer`, `llm-integration-engineer`, `frontend-engineer`, `test-engineer` e `etapa-verifier`. Rode o `etapa-verifier` antes de marcar uma etapa como concluída.
- **Skills:** `etapa-workflow`, `ffmpeg-video`, `langchain-structured`, `mediapipe-face`.
