# Frontend do editor de vídeos

Interface web (Vite + React + TypeScript) sobre a API em `../API`. Tema escuro, sem framework
de CSS: as cores e o espaçamento são variáveis em `src/index.css`.

## Instalar

```bash
cd frontend
npm install
```

## Rodar

Suba a API antes (em outro terminal):

```bash
cd API
uv run python -m src.api --port 8000
```

Depois:

```bash
cd frontend
npm run dev
```

Abra http://localhost:5173. Em dev, o Vite repassa `/api`, `/docs` e `/openapi.json` para
`http://127.0.0.1:8000` (outra porta: `API_URL=http://127.0.0.1:8001 npm run dev`).

Em "Abrir pasta", na faixa de clipes, use um caminho absoluto (ex.: a pasta `samples` na raiz do
repositório): caminhos relativos são resolvidos a partir da pasta onde a API foi iniciada.

## Build

```bash
npm run build     # checa tipos (tsc) e gera dist/
npm run preview   # serve dist/ em http://localhost:4173, com o mesmo proxy
```

## Organização

A tela tem quatro áreas (grid em `App.tsx`): cabeçalho, palco, faixa de clipes e coluna de opções,
com a barra de projetos na base. Abaixo de 1100 px elas empilham numa coluna só.

- `src/api.ts`: cliente tipado; os tipos espelham `API/src/api/schemas.py`. **Toda chamada passa
  por aqui.**
- `src/usePlano.ts`: estado do plano criativo (imagens e zooms), com as edições que não chamam o LLM.
- `src/components/`:
  - `Header.tsx`: marca, nome do projeto (edição inline), estado da API e botão "Ambiente";
  - `EnvironmentPanel.tsx`: gaveta com a configuração e as checagens do doctor;
  - `Workspace.tsx`: estado do projeto aberto, criação de jobs e polling de 1 s;
  - `Stage.tsx`: o palco, com três modos — resultado (player do vídeo final), clipe selecionado
    (`ClipDetails.tsx`: vídeo, transcrição, rosto) e plano criativo (`ImagePlan.tsx`);
  - `ClipStrip.tsx`: faixa horizontal de clipes, com arrastar para reordenar (e ← → no teclado),
    importar pasta, enviar vídeos e remover;
  - `OptionsPanel.tsx`: opções do pipeline (cortes, modelo do LLM, 9:16, legendas, imagens, zooms)
    e os botões de prévia e render;
  - `JobPanel.tsx`: job atual (etapa, progresso, tempo, log, cancelar) e histórico;
  - `ProjectBar.tsx`, `ErrorBox.tsx`: projetos e mensagens de erro.

Enquanto um job do projeto está rodando, os controles que a API recusaria (409) ficam desabilitados.
