# Agente Editor de Vídeos: Etapas de Desenvolvimento

## Como usar este arquivo com o Claude Code

1. Coloque este arquivo na raiz do projeto.
2. Peça uma etapa por vez: *"Leia o etapas.md e implemente a Etapa N. Siga as decisões fixas e só marque como concluída se os critérios de aceite passarem."*
3. Ao terminar cada etapa, rode os testes, faça commit e só então peça a próxima.
4. Não pule etapas. Cada uma entrega algo utilizável e a seguinte depende dela.

---

## Visão geral

Aplicativo local que recebe vários clipes de vídeo em sequência e gera um vídeo vertical (1080x1920) editado:

- Corta silêncios, pausas e erros de fala
- Legendas estilizadas queimadas no vídeo
- Reenquadramento horizontal para vertical com rastreamento de rosto
- Zooms no rosto em momentos de ênfase
- Imagens sobre a fala (ex.: falou "frutas", aparece uma cesta de frutas) posicionadas onde não cobrem o rosto nem as legendas
- Múltiplos clipes numa timeline única, na ordem da pasta (`1.mp4`, `2.mp4`, ...) ou na ordem escolhida no app

**Princípio central:** o LLM decide *o quê* e *quando*; o código decide *onde* e *como*. O LLM nunca vê áudio nem vídeo, só texto transcrito com índice e tempo de cada palavra. Posicionamento, enquadramento e zoom saem de geometria.

---

## Decisões fixas (não reabrir sem motivo)

| Tema | Decisão |
|---|---|
| Linguagem | Python 3.11+ |
| Transcrição | `faster-whisper` local, `word_timestamps=True`, `language="pt"`, `vad_filter=True`. Sem custo de API |
| LLM | **LangChain** como camada de abstração de provedor. Padrão: OpenAI (modelo da família mini). Trocável por `.env` |
| Saída do LLM | Sempre estruturada: `with_structured_output(ModeloPydantic)`. Validação extra em código |
| Rastreio de rosto | MediaPipe Face Detection + suavização (EMA) e zona morta |
| Render | Duas passadas: OpenCV (crop dinâmico e zoom) e FFmpeg (overlays, legendas `.ass`, áudio) |
| Legendas | Arquivo `.ass` (não `.srt`), queimado com o filtro `ass=` |
| Imagens | Pexels (principal) e Pixabay (fallback); `rembg` para o efeito sticker |
| Ordenação de clipes | `natsort` (numérica, não alfabética) |
| Interface | PySide6, com `QListWidget` com drag-and-drop |
| Cache | Por hash do arquivo (transcrição e rastreio de rosto), em disco |
| Formato final | 1080x1920, 30 fps, áudio 48 kHz |

### Regras de uso do LangChain

- Use só `langchain-core` e os pacotes de provedor (`langchain-openai`, `langchain-anthropic`). **Não use** agents, chains legadas nem memória.
- Crie o modelo com `init_chat_model(LLM_MODEL, temperature=0)` e use `.with_structured_output()` com modelos Pydantic.
- Todo acesso ao LLM passa por `src/llm/client.py`. Nenhum outro módulo importa LangChain.
- Prompts ficam em arquivos `.md`/`.txt` em `src/llm/prompts/`, não no meio do código.

### Variáveis de ambiente (`.env.example`)

```env
LLM_MODEL=openai:gpt-5-mini       # confirmar o nome atual do modelo na doc do provedor
OPENAI_API_KEY=
ANTHROPIC_API_KEY=                # só se trocar de provedor
WHISPER_MODEL=large-v3-turbo      # use small/medium se for CPU
WHISPER_DEVICE=auto               # auto | cuda | cpu
PEXELS_API_KEY=
PIXABAY_API_KEY=
CACHE_DIR=.cache
```

### Estrutura de pastas alvo

```
editor-videos/
├── etapas.md
├── .env.example
├── pyproject.toml
├── src/
│   ├── config.py
│   ├── project.py          # modelo do projeto/timeline (Pydantic)
│   ├── clips.py            # listar e ordenar clipes
│   ├── cache.py
│   ├── transcribe.py
│   ├── cuts.py             # silêncios, erros de fala, remap de tempo
│   ├── face.py             # rastreio de rosto
│   ├── reframe.py          # crop 9:16 e zoom
│   ├── captions.py         # gerador de .ass
│   ├── images.py           # busca, rembg, posicionamento
│   ├── render.py           # passadas de render e concatenação
│   ├── llm/
│   │   ├── client.py
│   │   ├── schemas.py
│   │   └── prompts/
│   └── ui/
├── tests/
└── samples/                # vídeos curtos para teste (não versionar)
```

---

## Etapa 0: Setup do projeto

**Objetivo:** esqueleto rodando, com dependências e verificação do ambiente.

**Tarefas**
- Criar a estrutura de pastas acima e o `pyproject.toml` com as dependências: `faster-whisper`, `mediapipe`, `opencv-python`, `numpy`, `pydantic`, `python-dotenv`, `natsort`, `langchain-core`, `langchain-openai`, `langchain-anthropic`, `requests`, `rembg`, `PySide6`, `pytest`.
- `src/config.py` lê o `.env` e expõe as configurações tipadas.
- Comando de verificação `python -m src.doctor`: confere se FFmpeg/ffprobe estão no PATH, se a GPU está disponível e se as chaves de API existem.
- Configurar logging padrão (arquivo + console).

**Critérios de aceite**
- `python -m src.doctor` imprime o status de cada item sem quebrar quando algo falta.
- `pytest` roda (mesmo com poucos testes).

---

## Etapa 1: Modelo de projeto e lista de clipes

**Objetivo:** representar o projeto (clipes em ordem) e persistir em JSON.

**Tarefas**
- `project.py`: modelos Pydantic para `Project`, `Clip`, `Timeline` (formato do JSON abaixo).
- `clips.py`: `list_clips_from_folder(path)` com ordenação natural (`natsort`), aceitando `.mp4`, `.mov`, `.mkv`.
- Ler metadados de cada clipe via `ffprobe` (duração, resolução, fps, tem áudio).
- Salvar e carregar o projeto em `project.json`; reordenar e remover clipes recalcula os `offset`.
- `cache.py`: hash do arquivo (ex.: SHA-256 dos primeiros/últimos MB + tamanho, para ser rápido) e helpers de leitura/escrita de cache.

**Formato da timeline**
```json
{
  "clipes": [
    {"arquivo": "1.mp4", "trechos": [[0.4, 8.2], [9.1, 15.0]], "offset": 0.0},
    {"arquivo": "2.mp4", "trechos": [[0.2, 12.5]], "offset": 13.7}
  ],
  "imagens": [{"inicio": 3.4, "duracao": 2.0, "query": "fruit basket"}],
  "zooms": [{"inicio": 5.0, "duracao": 1.5}],
  "legendas": "legendas.ass"
}
```

**Critérios de aceite**
- Numa pasta com `1.mp4, 2.mp4, 10.mp4`, a ordem é 1, 2, 10.
- Reordenar clipes muda a lista e recalcula offsets corretamente (teste unitário).

---

## Etapa 2: Transcrição por clipe

**Objetivo:** transcrição com timestamp por palavra, em cache.

**Tarefas**
- `transcribe.py`: extrair o áudio com FFmpeg (16 kHz mono) e transcrever com `faster-whisper` (`word_timestamps=True`, `language="pt"`, `vad_filter=True`).
- Usar `initial_prompt` com exemplos de hesitação ("é...", "hm", "né") para o Whisper não "limpar" essas ocorrências, já que queremos cortá-las.
- Saída: lista de palavras `{indice, texto, inicio, fim}` por clipe, salva em cache pelo hash do arquivo.
- Modelo e device vêm do `.env`.

**Critérios de aceite**
- Rodar duas vezes no mesmo clipe: a segunda usa o cache e leva menos de 1 s.
- Os timestamps de palavras conferem com o áudio numa checagem manual em 3 pontos do vídeo.

---

## Etapa 3: Cortes por silêncio e remap de tempo

**Objetivo:** primeiro vídeo utilizável, sem LLM: clipes concatenados sem pausas.

**Tarefas**
- `cuts.py`: calcular os trechos mantidos por clipe usando os gaps entre palavras e o `silencedetect` do FFmpeg. Parâmetros configuráveis: silêncio mínimo (padrão ~0,4 s) e margem de respiro (padrão ~0,08 s antes e depois de cada fala).
- Aparar silêncio no fim de um clipe e no começo do próximo, para não sobrar pausa na emenda.
- Função de **remap de tempo**: converte timestamp original em timestamp do vídeo cortado, somando o offset acumulado dos clipes anteriores. Cobrir com testes.
- `render.py` (versão simples): cortar e concatenar os trechos com FFmpeg, normalizando para 30 fps e áudio 48 kHz, com fade de áudio de 20 a 30 ms em cada emenda.

**Critérios de aceite**
- Vídeo final sem pausas longas e sem estalos nas emendas.
- Testes do remap de tempo passando, incluindo casos com vários trechos e vários clipes.

---

## Etapa 4: Camada LLM (LangChain) e corte de erros de fala

**Objetivo:** infraestrutura de LLM trocável e o primeiro uso dela: marcar falsos começos, repetições e takes errados.

**Tarefas**
- `llm/client.py`: função única `run_structured(prompt_name, input, schema)` usando `init_chat_model` + `with_structured_output`. Provedor e modelo vêm de `LLM_MODEL`.
- `llm/schemas.py`: modelo Pydantic `CortesFala` (lista de intervalos `[indice_inicio, indice_fim]` de palavras a remover, com motivo curto).
- Prompt em `llm/prompts/cortes_fala.md`: recebe a transcrição indexada e devolve só os índices a cortar (falsos começos, repetições, "errei, vou de novo").
- Validação em código: índices existem, intervalos não se sobrepõem, não remove mais que um limite configurável (ex.: 30% do clipe), senão descarta e avisa.
- Integrar em `cuts.py`: os trechos a remover entram no cálculo dos trechos mantidos.
- Retry com backoff em caso de falha de rede ou de schema.
- Testes com o LLM mockado.

**Critérios de aceite**
- Trocar `LLM_MODEL` entre dois provedores não exige mudar código fora do `.env`.
- Resposta inválida do LLM nunca quebra o pipeline: cai no comportamento da Etapa 3 e registra um aviso.

---

## Etapa 5: Rastreio de rosto

**Objetivo:** posição e tamanho do rosto por frame, suavizados e em cache.

**Tarefas**
- `face.py`: MediaPipe Face Detection frame a frame (dá para amostrar a cada 2 a 3 frames e interpolar).
- Suavização com EMA e zona morta para ignorar pequenos movimentos.
- Quando nenhum rosto é detectado: manter a última posição conhecida; se o clipe começar sem rosto, centralizar.
- Vários rostos: escolher o maior e mais central.
- Rastreio **isolado por clipe** (a suavização reinicia a cada clipe). Cache por hash do arquivo.
- Comando de debug que gera um vídeo com a caixa do rosto desenhada, para inspeção visual.

**Critérios de aceite**
- No vídeo de debug, a caixa acompanha o rosto sem tremer.
- Segunda execução usa o cache.

---

## Etapa 6: Reenquadramento vertical e render em duas passadas

**Objetivo:** vídeo 9:16 com o rosto centralizado.

**Tarefas**
- `reframe.py`: janela de crop 9:16 centrada no rosto, com câmera suave (movimento limitado por frame) e sem sair dos limites do vídeo.
- Passada 1 (OpenCV): lê o clipe (só os trechos mantidos), aplica o crop dinâmico, redimensiona para 1080x1920 e envia os frames para o FFmpeg por pipe.
- Passada 2 (FFmpeg): junta o vídeo com o áudio original, aplica os fades das emendas e concatena os clipes.
- Estruturar `render.py` para que legendas, imagens e zooms entrem depois como etapas adicionais sem reescrever o que já existe.

**Critérios de aceite**
- Vídeo final 1080x1920, 30 fps, áudio sincronizado (verificar no começo, meio e fim).
- Sem tremor visível na câmera.

---

## Etapa 7: Legendas estilizadas

**Objetivo:** legendas queimadas, palavra por palavra.

**Tarefas**
- `captions.py`: gerar `.ass` a partir das palavras já remapeadas para o tempo final.
- Agrupar em linhas curtas (ex.: 2 a 4 palavras), com destaque da palavra atual (cor diferente).
- Estilo configurável: fonte, tamanho, cor, contorno, sombra, posição (terço inferior, acima da área de UI das redes sociais).
- Exportar a **caixa da área de legenda** (retângulo em pixels) para a Etapa 8 evitar sobreposição.
- Queimar com o filtro `ass=` na passada 2; garantir que a fonte usada esteja disponível (incluir `fonts/` no projeto).

**Critérios de aceite**
- Legendas sincronizadas com a fala e legíveis em fundo claro e escuro.
- Nenhuma legenda atravessa a emenda entre clipes.

---

## Etapa 8: Imagens sobre a fala

**Objetivo:** imagens aparecendo no momento certo e no lugar certo.

**Tarefas**
- `llm/schemas.py` e prompt `plano_imagens.md`: o LLM recebe a transcrição **global** (todos os clipes, tempos finais) e devolve `[{palavra, indice, query, duracao}]`.
  - Limite de densidade: no máximo 1 imagem a cada 3 a 5 s, só para substantivos concretos, sem repetir a mesma imagem em sequência.
  - Queries em inglês (melhor resultado nas APIs de stock).
- `images.py`:
  - Busca no Pexels, fallback Pixabay, com download em cache local.
  - `rembg` opcional para o efeito sticker (PNG com fundo transparente).
  - **Posicionamento:** definir zonas candidatas no quadro 1080x1920 (topo, laterais, acima das legendas), descartar as que intersectam a caixa do rosto (Etapa 5) ou da legenda (Etapa 7) e escolher a maior zona livre. O LLM nunca define coordenadas.
  - Iniciar a imagem ~0,2 s antes da palavra; fade/zoom leve de entrada e saída.
- Validação em código: tempos dentro do clipe onde a palavra foi dita, densidade respeitada, duração entre 1,2 e 3 s.
- Aplicar com `overlay` + `enable='between(t,a,b)'` no FFmpeg.
- **Modo preview:** gerar a lista de sugestões (palavra, imagem escolhida) para o usuário aprovar ou trocar antes do render final.

**Critérios de aceite**
- Nenhuma imagem cobre o rosto ou as legendas.
- Nenhuma imagem atravessa a emenda entre clipes.
- Trocar uma imagem no preview e re-renderizar não chama o LLM de novo.

---

## Etapa 9: Zooms no rosto

**Objetivo:** ênfase visual em momentos-chave.

**Tarefas**
- Estender o schema do plano criativo com `zooms: [{indice_palavra, duracao}]` (o LLM marca frases de ênfase; limite: no máximo 1 zoom a cada ~8 s).
- `reframe.py`: o zoom é a mesma janela de crop com escala maior (1.0 → 1.15) e easing de entrada e saída, centrada no rosto rastreado.
- Não permitir zoom que corte o rosto ou ultrapasse as bordas.
- Zooms não atravessam emendas de clipes.

**Critérios de aceite**
- Zoom suave, sem salto e sem tremor.
- Com zoom e imagem ao mesmo tempo, o posicionamento da imagem continua correto.

---

## Etapa 10: Interface (PySide6)

**Objetivo:** usar tudo sem terminal.

**Tarefas**
- Tela principal: botão "Abrir pasta" (preenche a lista via ordenação natural) e "Adicionar vídeos" (ordem de seleção).
- `QListWidget` com miniatura, arrastar para reordenar, remover.
- Opções: ligar/desligar cortes, legendas, imagens e zooms; estilo da legenda; modelo do LLM.
- Botão "Gerar preview" (mostra as sugestões de imagem para aprovar) e "Renderizar".
- Processamento em thread separada (`QThread`), com barra de progresso por etapa e botão cancelar.
- Salvar e abrir projeto (`project.json`).

**Critérios de aceite**
- Reordenar na lista e renderizar reflete a nova ordem no vídeo.
- A interface não trava durante o processamento.

---

## Etapa 11: Polimento e robustez

**Tarefas**
- Mensagens de erro claras: FFmpeg ausente, chave de API inválida, clipe sem áudio, clipe sem rosto.
- Fila de jobs para renderizar vários projetos em sequência.
- Logs por execução (tempo de cada etapa, tokens usados e custo estimado do LLM).
- Testes de integração com 2 clipes curtos em `samples/`.
- `README.md` com instalação, `.env` e uso.
- Empacotamento opcional (PyInstaller) para rodar sem instalar Python.

**Critérios de aceite**
- Fluxo completo com 3 clipes, do zero até o vídeo final, sem intervenção manual além da aprovação do preview.

---

## Checklist geral

- [x] Etapa 0: Setup
- [x] Etapa 1: Projeto e lista de clipes
- [x] Etapa 2: Transcrição
- [x] Etapa 3: Cortes por silêncio e remap de tempo
- [x] Etapa 4: Camada LLM e cortes de erros de fala
- [x] Etapa 5: Rastreio de rosto
- [ ] Etapa 6: Reenquadramento vertical
- [ ] Etapa 7: Legendas
- [ ] Etapa 8: Imagens
- [ ] Etapa 9: Zooms
- [ ] Etapa 10: Interface
- [ ] Etapa 11: Polimento
