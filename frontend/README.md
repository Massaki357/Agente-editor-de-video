# Frontend do editor de vídeos

Interface web (Vite + React + TypeScript) para testar a API em `../API`. Design provisório.

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

Na tela "Importar pasta", use um caminho absoluto (ex.: a pasta `samples` na raiz do repositório):
caminhos relativos são resolvidos a partir da pasta onde a API foi iniciada.

## Build

```bash
npm run build     # checa tipos (tsc) e gera dist/
npm run preview   # serve dist/ em http://localhost:4173, com o mesmo proxy
```

## Organização

- `src/api.ts`: cliente tipado; os tipos espelham `API/src/api/schemas.py`. Toda chamada passa por aqui.
- `src/components/`: cabeçalho (saúde, doctor, config), lista de projetos, projeto (clipes, ações,
  job com polling de 1 s, resultado).
