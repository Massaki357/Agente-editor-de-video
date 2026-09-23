# Editor de Vídeos

App local que transforma clipes em um vídeo vertical 1080x1920 editado. O sistema tem duas partes: uma **API FastAPI** em `API/`, com todo o processamento, e um **frontend web** em `frontend/`, que só conversa com a API.

O plano, as decisões fixas e o checklist ficam em `etapas.md`, que é a fonte da verdade. Implemente uma etapa por vez com a skill `etapa-workflow`. Testes que dependem de vídeos do usuário ou de checagem manual ficam em `testes-pendentes.md`.

## Layout
- `API/`: backend Python 3.12 com `uv`: `src/` (núcleo + `src/api/`), `tests/`, `pyproject.toml`, `.cache/` e `data/` (projetos da API). **Todo comando `uv` roda dentro de `API/`.**
- `frontend/`: Vite + React + TypeScript. Todas as chamadas passam por `frontend/src/api.ts`; em dev, o Vite faz proxy de `/api` para `http://127.0.0.1:8000`. O layout (Etapa 10) é o `App.tsx` em grid: cabeçalho, palco (`Stage.tsx`: resultado, clipe ou plano criativo), faixa de clipes (`ClipStrip.tsx`, arrastar para reordenar), coluna de opções e job (`OptionsPanel.tsx` + `JobPanel.tsx`) e barra de projetos (`ProjectBar.tsx`); o `Workspace.tsx` cuida do estado do projeto aberto. Tema escuro com variáveis em `index.css`.
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
- Configuração só via `src.config.get_settings()`. O modelo do LLM pode ser trocado por execução em `PipelineOptions.llm_model` (`Settings.for_model`); `GET /api/config` lista os escolhíveis (`Settings.available_llm_models`, só provedores com chave). O python-dotenv lê `KEY=   # comentário` como valor, e `config._clean` trata isso.
- Processamento pesado na API é sempre um **job** (`API/src/api/tasks.py`), numa fila com um worker. Um projeto com job ativo não pode ser alterado (a API responde 409). Toda funcionalidade nova entra no núcleo (`src/`), é exposta na API (com testes em `tests/test_api.py`) e ganha um controle mínimo no frontend.
- Legendas: `.ass` gerado por `src/captions.py` a partir das palavras em t_out, **por clipe** (nunca atravessa emenda), e queimado na passada 2 do render (`_second_pass`, filtro `ass=`). A fonte (Poppins Bold, OFL) fica em `API/fonts/` e é copiada para a pasta de trabalho do render.
- Imagens (`src/images.py`): o LLM só escolhe as palavras e a query; o código faz a validação (densidade, 1,2–3 s, sem atravessar emenda), a busca (Pexels, com o Pixabay como alternativa, e cache) e o posicionamento (zonas livres de rosto e legenda). O plano criativo (imagens + zooms, uma chamada só ao LLM com o prompt `plano_criativo`) fica em `API/data/projects/<id>/plano_imagens.json`: o job `imagens` é a prévia, e o `gerar` reaproveita o plano se a `assinatura` (trechos) não mudou.
- Zooms (Etapa 9): o LLM aponta a palavra de ênfase; `images.validate_zooms` aplica as regras (1 a cada 8 s, 1–2,5 s, sem atravessar emenda). A geometria fica em `src/reframe.py`: `plan_zooms` fixa o pico de cada zoom (até 1,15, limitado para a caixa real do rosto caber com folga; frames sem detecção seguem o caminho da câmera), `zoom_curve` dá a escala por t_out (smoothstep) e `CameraPath.window_f(t, escala)` é a janela fracionária, recortada no render com `cv2.warpAffine` (sem tremor de 1 px). O `face_boxes_fn` recebe a mesma curva, e as imagens desviam do rosto já ampliado.
- Erros e avisos (Etapa 11): o job guarda `src.errors.mensagem_amigavel(exc)` (causa provável + o que fazer, com o detalhe técnico no fim) e o resultado traz `avisos` (sem áudio, fps variável, HDR, rosto quase não detectado) e `llm` (chamadas, tokens e custo estimado por `src/llm/pricing.py`). Etapas longas recebem `on_progress(fracao)`, que também cancela (o `ctx.step` lança): quem chama o callback precisa deixar a exceção passar — o Whisper marca com `_ProgressoAbortado` para não cair no fallback GPU→CPU, e o `render_debug` mata o ffmpeg antes de propagar.
- Fontes difíceis: `probe_clip` marca `vfr` e `hdr`; o render converte HDR para BT.709 com tonemapping e reduz fontes muito maiores que a saída antes do pipe (`_render_segment_reframe`).
- Com `frontend/dist` compilado, a API serve a interface em `/` (`api.app.montar_frontend`).
- Trechos (`Clip.trechos`) ficam na grade de frames (1/30 s): `cuts.TimeMap` e o render produzem exatamente os mesmos tempos.
- Rosto:
  - O mediapipe 1.x não tem `mp.solutions`: use `mediapipe.tasks.python.vision.FaceDetector`.
  - O BlazeFace não enxerga rostos de tamanho normal num quadro 16:9 inteiro; `face.square_crops` resolve isso detectando em recortes quadrados.
  - `FaceTrack.cx/cy/w/h` é o caminho suave da câmera (Etapas 6 e 9); `FaceTrack.box_at(t)` é a caixa real do rosto (Etapas 8 e 9).

## Ferramentas novas (novas-etapas.md)
- O `novas-etapas.md` traz cinco ferramentas em desenvolvimento (áudio, estabilização, b-roll, legendas de destaque e edição pós-render). Mesma regra: uma etapa por vez, com verificação antes de marcar.
- Áudio (`API/src/audio/`): o DeepFilterNet roda pelo **binário oficial** baixado para `CACHE_DIR/models/` (o pacote do PyPI não tem wheel para o Python 3.12); `metrics.measure` dá piso de ruído, nível de fala e SNR, e é assim que os testes provam que a limpeza funcionou. A cadeia é `optimize.optimize_audio`: highpass 80 Hz → `denoise.denoise` (DeepFilterNet → noisereduce → afftdn, caindo para o próximo se um falhar) → `loudness.normalize` (loudnorm em 2 passadas, −16 LUFS), com cache por hash do arquivo + parâmetros + `CADEIA_VERSION`. O cache guarda o motor usado: se ele for pior que o disponível agora (o binário do DeepFilterNet é baixado no 1º uso), a cadeia refaz em vez de devolver o áudio pior; e um download que falhou fica marcado na sessão (`deepfilter.pode_usar`), para não retentar a cada clipe. Depois do loudnorm o ruído absoluto engana (o ganho sobe tudo): compare **SNR**.

## Automação do Claude Code (.claude/)
- **Hooks:**
  - bloqueiam edição de `.env`, `uv.lock`, vídeos em `samples/` e `.cache/`;
  - formatam `.py` de `API/` com ruff e bloqueiam LangChain fora do client;
  - rodam o pytest (em `API/`) ao fim do turno se algum `.py` mudou;
  - mostram o progresso das etapas ao iniciar a sessão.
- **Subagentes:** `video-pipeline-engineer`, `llm-integration-engineer`, `frontend-engineer`, `test-engineer` e `etapa-verifier`. Rode o `etapa-verifier` antes de marcar uma etapa como concluída.
- **Skills:** `etapa-workflow`, `ffmpeg-video`, `langchain-structured`, `mediapipe-face`.
