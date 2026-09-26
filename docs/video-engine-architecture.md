# Arquitetura do motor de vídeo (auditoria para Caption Style Engine + Transition Engine)

> Gerado na Etapa 1 de `PLANO_CAPTIONS_TRANSITIONS_CLAUDE_CODE.md`. Descreve o pipeline
> **como ele existe hoje** no código (não como deveria ser). Todos os caminhos são
> relativos a `API/`.

## 1. Visão geral e ponto de entrada

Há três formas de disparar o pipeline, todas convergindo em `src/pipeline.py`:

- **CLI**: `python -m src.pipeline entrada1 entrada2 -o saida.mp4` → `pipeline.main()` →
  `pipeline.run()` → `pipeline.render_project()`.
- **API**: job `"gerar"` (`src/api/tasks.py:gerar`, por volta da linha 200) carrega o
  `Project` salvo e chama `pipeline.render_project()` com as `PipelineOptions` do job.
  Outros jobs (`substituir`, `desfazer`, `refazer`, `aplicar_edicao`, `previsualizar`)
  também acabam chamando `render_project`, passando `render_cache_dir` para usar o
  caminho **incremental** em vez do caminho **completo**.
- **Render de baixo nível isolado**: `python -m src.render project.json -o saida.mp4`
  (`src/render.py:main`) — não aplica cortes, plano criativo, zooms, B-roll nem
  legendas; só concatena os trechos já definidos em `Timeline.clipes[].trechos`. Serve
  para depuração, não é o caminho de produção.

`pipeline.render_project()` (`src/pipeline.py:314`) é o orquestrador central. Em ordem:

1. `apply_project_cuts` — cortes de silêncio + LLM (`src/cuts.py`), define `Clip.trechos`.
2. `project_video_estabilizado` — cópia do projeto com vídeo estabilizado (Parte 2 do
   `novas-etapas.md`), se `estabilizar=True`.
3. `face_tracks` + `reframe_cameras` — rastreio de rosto e janela 9:16 por clipe
   (`src/face.py`, `src/reframe.py`), se `reenquadrar`/`imagens`/`legendas_destaque`.
4. `make_captions` — legenda contínua em `.ass` (`src/captions.py`), se
   `legendas_continuas`.
5. `image_plan` — plano criativo único do LLM: imagens, zooms, B-roll e destaques
   (`src/images.py:plan_images`, `src/highlight_captions/planner.py`).
6. Zooms: `reframe.plan_zooms` + `zoom_curve` a partir dos intervalos do plano.
7. Imagens: `images.build_overlays` → lista de `Overlay` (PNG posicionado + intervalo).
8. Legenda de destaque (se `legendas_destaque`): `highlight_captions.ass_builder`,
   desviando das caixas de rosto e das imagens.
9. B-roll: `broll.transitions.select_items` + `prepare_cutaways` → lista de `Cutaway`.
10. `audios_limpos` — WAV limpo por clipe (Parte 1 do `novas-etapas.md`), se
    `limpar_audio`.
11. Atualiza `project.documento` (o "documento de edição", ver §7).
12. Chama `render_timeline` (caminho completo, `src/render.py`) **ou**
    `render_incremental` (`src/editing/incremental_render.py`), conforme
    `render_cache_dir` seja `None` ou não.

## 2. FFmpeg — todos os pontos de invocação

| Arquivo | O que faz |
|---|---|
| `src/render.py:_render_segment` (293) | Passada 1 sem reenquadrar: decodifica+recodifica um trecho com letterbox, filtros de cor (`_decode_color_filter`), fades de áudio. |
| `src/render.py:_render_segment_reframe` (409) | Passada 1 com reenquadrar: **dois** processos FFmpeg (decode → pipe cru BGR24 → OpenCV recorta → pipe cru → encode), ver §6. |
| `src/render.py:_concat` (547) | Concat demuxer (`-f concat`), vídeo copiado (`-c:v copy`), áudio recodificado em AAC uma vez. |
| `src/render.py:_second_pass` (589) | `filter_complex` com overlays de imagem (fade + leve zoom de entrada) e o filtro `ass=` para legendas, sobre o vídeo já concatenado. **Roda com `cwd` na pasta temporária** por causa de caminhos do Windows em filtros. |
| `src/broll/transitions.py:apply_cutaways` (175) | `filter_complex` com `trim`/`concat`/`xfade` para inserir cutaways de B-roll com transição configurável; roda **entre** `_concat` e `_second_pass`. |
| `src/broll/catalog.py:render_preview` / `installed_xfade_effects` | Gera prévias sintéticas de cada preset e consulta `ffmpeg -h filter=xfade` para saber quais efeitos xfade existem nesta instalação. |
| `src/broll/source.py` | Normaliza clipe de B-roll baixado para 1080x1920@30fps sem áudio (não lido nesta auditoria em detalhe, mas é FFmpeg). |
| `src/audio/*.py` | Cadeia de limpeza de áudio (highpass, denoise, loudnorm) — todos filtros/CLIs FFmpeg ou o binário do DeepFilterNet. Fora do escopo visual, mas compartilha `_run_ffmpeg`/`FFMPEG_BASE` conceitualmente (cada módulo tem sua própria função de execução). |
| `src/video/stabilize.py`, `opencv_fallback.py` | Estabilização (`vidstabdetect`/`vidstabtransform` ou fallback OpenCV) — fora do escopo visual desta auditoria. |
| `src/clips.py` | `ffprobe` para metadados (`ClipMeta`), detecção de VFR/HDR. |
| `src/doctor.py` | Checa binários e filtros (`REQUIRED_FFMPEG_FILTERS = ["ass", "silencedetect", "overlay", "afade", "concat"]`). **`xfade` não está nessa lista** — é checado só sob demanda em `installed_xfade_effects()`, com fallback silencioso para corte seco. |

Todas as chamadas a `subprocess.run(["ffmpeg", ...])` usam a lista `FFMPEG_BASE =
["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]` de `render.py`, exceto
os módulos de áudio/vídeo que têm suas próprias constantes equivalentes.

## 3. OpenCV — todos os pontos de uso

| Arquivo | O que faz |
|---|---|
| `src/render.py:_crop` / `_crop_subpixel` (513, 520) | Recorta a janela da câmera de um frame cru (numpy) e redimensiona (`cv2.resize`, `cv2.INTER_CUBIC`/`INTER_AREA`). `_crop_subpixel` usa **`cv2.warpAffine`** com uma matriz 2x3 (translação + escala) para recortar em coordenadas fracionárias sem tremor de 1 px durante o zoom. **Não há `cv2.warpPerspective` em nenhum lugar do projeto** — só transformações afins (translação/escala), nunca perspectiva/rotação 3D. |
| `src/face.py` | MediaPipe Tasks (`FaceDetector`) para detecção, mais operações OpenCV auxiliares (`square_crops`, redimensionamento dos recortes quadrados enviados ao detector). |
| `src/video/opencv_fallback.py:138` | `cv2.warpAffine` para compensar o tremor de câmera (estabilização), com `cv2.goodFeaturesToTrack` + `cv2.calcOpticalFlowPyrLK` para o rastreio de pontos. |

OpenCV nunca decodifica ou codifica vídeo — ele só processa frames crus (BGR24) que
chegam por pipe de um FFmpeg decodificador e devolve frames crus para um FFmpeg
codificador. Isso é central para o desenho de memória constante do render (ver §1 do
docstring de `render.py`).

## 4. Legendas ASS — os dois motores existentes (ponto-chave para a Etapa 2)

**Não existe hoje uma abstração de "Caption Style Engine".** Existem **dois geradores de
`.ass` independentes**, cada um com sua própria classe de estilo, seu próprio layout e
sua própria lógica de tempo. Eles compartilham só primitivas de baixo nível.

### 4.1 Legenda contínua — `src/captions.py`

- `CaptionStyle` (Pydantic): fonte (só Poppins Bold hoje, com validação que a trava),
  tamanho, cor, cor de destaque, contorno, sombra, margens, maiúsculas, palavras por
  grupo, pausa que força quebra, escala de destaque da palavra atual.
- `group_words()`: agrupa palavras de UM clipe (em t_out) em blocos de até
  `palavras_max`, fechando em pontuação final, pausa longa ou estouro de largura
  (medida com a fonte real via Pillow, `text_width`).
- `build_ass()`: monta o cabeçalho `[V4+ Styles]` com **um único** `Style: Legenda` e um
  evento **por palavra**, com a palavra atual em cor/escala diferentes (efeito
  "karaokê" simples, sem tags de movimento).
- Helpers de baixo nível reaproveitados por outros módulos: `_cor_ass`, `_cor_tag`,
  `_escape_ass`, `_tempo_ass` (arredonda para baixo, de propósito — ver comentário em
  `_tempo_ass`), `libass_scale`/`ass_font_size` (conversão do tamanho em px para o
  `Fontsize` do `.ass`, calibrada pela métrica OS/2 da fonte).
- `CaptionStyle.box()` devolve a área ocupada em pixels, usada por `images.py` e por
  `highlight_captions` para não cobrir a legenda com imagens/destaques.

### 4.2 Legenda de destaque — `src/highlight_captions/`

- `HighlightStyle` (Pydantic, `style.py`): fonte, tamanho (72 px padrão vs. 84 da
  contínua), cor, cor de entrada, contorno, sombra, margem lateral, duração de
  permanência após a fala, fade de saída. **Reaproveita `ARQUIVO_FONTE`/`FONTS_DIR` de
  `captions.py`, mas é uma classe Pydantic totalmente separada** (nenhum campo comum
  além do que foi copiado manualmente).
- `planner.py`: schema do LLM (`DestaqueSugerido`/`PlanoDestaques` em
  `src/llm/schemas.py`) + validação (até 5 palavras consecutivas da mesma frase, sem
  sobreposição, espaçamento mínimo).
- `ass_builder.py`: **um segundo `build_ass` inteiro**, com seu próprio cabeçalho
  (`Style: Destaque`, `Alignment: 8` — topo — contra `Alignment: 2` — base — da
  contínua), sua própria lógica de posicionamento (`_layout`, `_posicao`, que desvia de
  caixas ocupadas por rosto/imagens via `caixas_ocupadas`) e sua própria revelação
  palavra a palavra + evento de permanência. Reaproveita só `_cor_ass`, `_escape_ass`,
  `_tempo_ass`, `libass_scale` de `captions.py` (importados diretamente, símbolos
  privados com `_`).

### 4.3 Exclusividade e acoplamento

- `Project` e `PipelineOptions` têm `legendas_continuas: bool` e `legendas_destaque:
  bool`, com um `model_validator` que rejeita as duas `True` ao mesmo tempo — em
  **ambos** os modelos (`src/project.py:172`, `src/pipeline.py:103`), então a regra é
  aplicada tanto ao salvar o projeto quanto ao criar as opções de um job.
- Na prática, só um dos dois `write_captions`/`write_highlights_project` roda por
  render (`render_project`, linhas 377 e 434), e o `.ass` resultante nunca tem as duas
  `Style:` ao mesmo tempo.
- A interface (`frontend/src/components/OptionsPanel.tsx`) já usa **um seletor único**
  (Nenhuma/Contínua/Destaque, linha ~305) que deriva as duas flags — não duas caixas
  independentes. Isso já implementa o padrão de UX que a Etapa 9 do plano novo pede;
  não precisa ser refeito, só estendido se novos modos aparecerem.

### 4.4 O que falta para uma "Caption Style Engine" de verdade

Hoje **não há**: registro de presets, seleção de "animação" desacoplada do estilo,
suporte a mais de uma fonte, nem um contrato comum entre os dois `build_ass`. Qualquer
"Etapa 2" precisa decidir (ver §9) se cria uma abstração que **envolve** os dois
geradores existentes (preservando-os como estão, plugáveis por um registry) ou se
generaliza um deles para cobrir os dois casos. O plano exige preservar o comportamento
atual como preset "legado/default" — isso aponta para a primeira opção.

## 5. Timestamps e sincronização

- **Tempo do clipe original**: `t_src`, em segundos, sempre alinhado à grade de frames
  (`k/fps`, 30 fps por padrão). `Clip.trechos: list[tuple[float, float]]` guarda pares
  `(início, fim)` em t_src, validados sem sobreposição e em ordem (`project.py:58`).
- **Tempo do vídeo final**: `t_out`. `Clip.offset` é a soma das durações mantidas dos
  clipes anteriores (`Timeline.recalcular_offsets`, `project.py:109`).
- **`src/cuts.py:TimeMap`** (639) é a ponte entre os dois:
  - `to_out(clip, t_src, snap)` → `t_out` (ou `None` se o tempo caiu num trecho
    cortado; `snap="next"/"prev"` arredonda para a borda mais próxima).
  - `to_src(t_out)` → `(índice do clipe, t_src)`.
  - `clip_bounds(clip)` → `(t_out início, t_out fim)` de um clipe inteiro.
  - `seams()` → lista de `t_out` das emendas entre clipes (**pontos onde nada pode
    atravessar**: nem legenda, nem imagem, nem zoom, nem B-roll, nem — hoje — qualquer
    efeito).
  - `words_to_out` / `visible_words` (fora da classe) — remapeiam palavras da
    transcrição, descartando fragmentos cortados abaixo de `RESTO_MAX` (40 ms).
- **`src/render.py:Segment`** é a unidade final de render: `inicio`/`fim` em t_src (já
  na grade de frames), `t_out`, `frames` (contagem exata), mais `audio` (trilha
  alternativa) e `fade_in`/`fade_out` (booleanos, usados pelo render incremental para
  não duplicar fade numa borda que não é emenda real, ver §7).
- A sincronia A/V não deriva porque cada trecho intermediário tem **exatamente**
  `frames` quadros de vídeo e `frames/fps` segundos de áudio PCM (ver o docstring no
  topo de `render.py`); a concatenação copia vídeo e recodifica áudio uma única vez.

## 6. Zoom, rastreio facial e a interação com o render

- **`src/face.py`**: `FaceTrack` guarda dois caminhos por frame do clipe original: o
  caminho **suave da câmera** (`cx/cy/w/h`, EMA bidirecional + zona morta) e a
  **caixa real do rosto** (`box_at(t)`, para nunca cortar o rosto). Cache em
  `CACHE_DIR/face/` por hash do clipe + parâmetros.
- **`src/reframe.py:CameraPath`**: janela 9:16 (`window`/`window_f`) a partir do
  `FaceTrack`. `window_f(t_src, escala)` devolve a janela **fracionária** (não
  arredondada) — usada quando há zoom, para não tremer.
- **`plan_zooms`**: escolhe o pico de escala de cada zoom sugerido pelo LLM, limitado
  por `face_zoom_limit` (a caixa real do rosto tem que caber com folga). `zoom_curve`
  transforma isso numa função `t_out → escala` com transições suaves (`smoothstep`).
- **Onde isso entra no render** (`_render_segment_reframe`, `render.py:409`): para cada
  frame `k` do trecho, calcula `escala = zoom(t_out + k/fps)`; se há zoom ou a fonte é
  maior que a saída, usa `camera.window_f` + `_crop_subpixel` (`warpAffine`); senão usa
  `camera.window` (arredondado) + `_crop` (`cv2.resize` simples). Ou seja: **o zoom é
  aplicado por frame, dentro da passada 1**, antes de qualquer overlay ou legenda (que
  só existem na passada 2, já em coordenadas de saída).
- Um cutaway de B-roll **substitui a imagem inteira** durante seu intervalo — não
  precisa (e não usa) câmera nem zoom, porque ocupa a tela toda (ver
  `broll/transitions.py`).

## 7. Cortes/clipes: representação interna

- `Clip` (`project.py:51`): `id` estável (`clip_xxxxxxxxxxxx`), `arquivo`, `trechos`,
  `offset`, `meta` (`ClipMeta`: duração, largura, altura, fps, tem_audio, rotação, vfr,
  hdr).
- `Timeline`: lista de `Clip` + (campos legados) `imagens`/`zooms`/`legendas` — hoje
  **não usados para novos projetos** (o plano criativo vive em `documento`, ver
  abaixo), mantidos só para migração de projetos antigos.
- **`DocumentoEdicao`** (`src/editing/project_schema.py:50`) é o "documento de edição"
  introduzido na Parte 5 do `novas-etapas.md`: uma lista plana de `Elemento`, cada um
  com `id` estável, `tipo` (`Literal["clipe", "corte", "crop", "imagem", "zoom",
  "broll", "legenda", "destaque"]` — **não há tipo "transição"**), intervalo `(inicio,
  fim)` em t_out, `ativo`/`obsoleto`, `origem` (`timeline`/`timeline_legada`/
  `plano`/`render`) e um `dados: dict` com o payload específico do tipo.
  - Isso é o que a interface de edição pós-render, o histórico (undo/redo) e o chat de
    ajustes usam para localizar e alterar qualquer coisa **por ID**.
  - `sincronizar_timeline` reconstrói os elementos `clipe`/`corte` a partir da
    `Timeline` a cada save/load, preservando os IDs dos demais tipos.
  - `com_plano` substitui os elementos de origem `"plano"` (imagem/zoom/broll/destaque)
    quando um novo plano criativo é gerado.
  - `eventos_ass` faz engenharia reversa do `.ass` de legenda contínua **depois de
    gerado**, criando um `Elemento` tipo `"legenda"` por evento visível — é assim que a
    legenda contínua vira algo endereçável por ID no chat/edição, sem precisar mudar
    `captions.py`.
  - `keyframes_crop` resume a janela real da câmera em keyframes de ~0,5 s — usado só
    para exibir a linha do tempo na aba Edição, não para o render em si.

## 8. Render incremental, segmentação e cache (crítico para o Transition Engine)

- **Cache de baixo nível** (`src/cache.py`): `file_hash` (SHA-256 de tamanho + 1 MB do
  início/fim, memoizado), `read_json_cache`/`write_json_cache` (atômico, arquivo
  temporário + `os.replace`), layout `CACHE_DIR/<tipo>/<chave>.json`. Usado por
  praticamente todo módulo (transcrição, rosto, imagens, áudio, vídeo).
- **Render completo** (`render_timeline`, sem `render_cache_dir`): sempre renderiza
  tudo do zero num diretório temporário; não tem cache próprio (o cache está nos
  passos *anteriores*: transcrição, rosto, plano, áudio limpo, vídeo estabilizado).
- **Render incremental** (`src/editing/segments.py` + `src/editing/
  incremental_render.py`, usado quando `render_cache_dir` é passado — API sempre usa
  este caminho para os jobs pós-primeira-geração):
  1. `plan_segments` (render.py) monta os `Segment` "base" (um por trecho mantido).
  2. `plan_edit_segments` (segments.py) **subdivide** cada `Segment` base nas bordas de
     qualquer elemento visível que o toque (imagem, zoom, broll — com uma margem de
     guarda de 0,5 s reservada mesmo em corte seco, para trocar só o efeito não mudar
     os limites do segmento —, destaque, legenda) e fatia lacunas longas em pedaços de
     até `max_gap` (3 s). Cada `SegmentoEdicao` carrega a tupla de `ids` de elementos
     que o afetam.
  3. `_hash_segment` (incremental_render.py:52) calcula uma chave SHA-256 por segmento
     a partir de: hash do arquivo fonte e do áudio alternativo, geometria do
     `RenderSettings`, todos os frames da janela da câmera + escala de zoom **daquele
     segmento**, o `dados` bruto de cada elemento com `id` no segmento, **o hash do
     cabeçalho do `.ass` antes de `[Events]`** (`ass_style`) + os eventos que caem
     naquele intervalo (`ass_events`) + hash de cada arquivo de fonte, os overlays que
     tocam o intervalo (com hash do PNG), e os cutaways de B-roll que tocam o
     intervalo (com hash do arquivo e os `TransitionConfig` de entrada/saída
     serializados) + o preset de transição padrão do projeto.
  4. Um segmento só é re-renderizado se: não existe `.mov` em cache com essa chave, OU
     seu conjunto de `ids` intersecta `ids_alterados` (passado explicitamente pela API
     de edição/chat), OU ele se sobrepõe a uma faixa de frames que o manifesto anterior
     apontava para um `id` que sumiu/mudou de posição.
  5. `_render_one` renderiza o segmento isolado (mesmas funções `_render_segment(_
     reframe)` do render completo) e, se há B-roll ou legenda/overlay **dentro daquele
     segmento**, aplica `apply_cutaways`/`_second_pass` só nele, com `time_offset` para
     os tempos absolutos do `.ass` continuarem batendo.
  6. `_concat` final costura os `.mov` (cache + recém-renderizados).

### Implicação direta para transições e legendas novas

**Qualquer mudança de conteúdo do `.ass` (novo preset, nova animação, novo parâmetro de
estilo) já invalida corretamente o cache**, porque o hash inclui o texto do cabeçalho de
estilo e os eventos — não é preciso subir uma "versão" manual em lugar nenhum, desde que
o gerador de `.ass` continue sendo determinístico e continue existindo só **um** `.ass`
por render. Isso reduz bastante o risco da Etapa 2–4 do plano novo em relação ao render
incremental.

**Transições genéricas entre clipes não têm o mesmo forro pronto.** O sistema de
transições existente (`broll/transitions.py`) é **inteiramente escopado a cutaways de
B-roll** — ele sabe recortar `trim`/`xfade`/`concat` **dentro de um único trecho
mantido**, nunca **entre dois trechos** (a emenda entre clipes, ou entre dois trechos
cortados do mesmo clipe, é hoje um ponto que **nada pode atravessar**: nem imagem, nem
zoom, nem B-roll — ver `_plan_cutaways`, que rejeita explicitamente um cutaway que
atravesse uma emenda). Se a Etapa 5+ do plano novo (`transition(clip_a=..., clip_b=...,
type="fade", ...)`) quer aplicar efeitos **nos cortes reais da timeline** (não só na
entrada/saída de um B-roll), isso é trabalho novo, não uma extensão trivial do código
atual — ver a questão em aberto no §9.

## 9. Transições — o que já existe vs. o que o plano novo pede

| | B-roll (`broll/transitions.py`, hoje) | Plano novo (Etapa 5+) |
|---|---|---|
| Onde aplica | Entrada/saída de um cutaway de B-roll, sempre dentro de um único trecho mantido | `clip_a`/`clip_b` — sugere qualquer par de segmentos adjacentes, inclusive a emenda real entre clipes |
| Emenda entre clipes | Nunca é tocada (regra explícita, `RESTO_MAX`/`seams` continuam hard cut) | Não fica claro se deveria continuar assim |
| Catálogo | 7 presets (`hard_cut`, `crossfade`, `slide`, `wipe`, `reveal`, `zoom`, `blur`) já implementados, com duração/direção/intensidade validadas e prévias | Etapa 6 pede 10 (incluindo Pixelize/Radial), Etapa 7 pede mais 9 estilizadas (whip, flash, shake, glitch, RGB split...) |
| Áudio | Nunca corta (a voz é o `.mov` da câmera concatenado; só o vídeo troca) | Mesmo requisito |
| Cache incremental | Integrado (hash inclui `TransitionConfig` por cutaway) | Precisaria do mesmo tratamento se aplicado a emendas |

**Isto é a decisão arquitetural que a Etapa 1 pede para não decidir sozinha:** dá para
tratar "transição entre clip_a e clip_b" como um cutaway sem clipe de B-roll de verdade
(ou seja, generalizar `Cutaway`/`apply_cutaways` para aceitarem "o próprio clipe
seguinte" como o "arquivo" a entrar), ou é preciso um mecanismo novo, porque hoje a
emenda entre clipes é uma fronteira dura em vários lugares (`_plan_cutaways` valida "sem
atravessar emenda"; `TimeMap.seams()` é usado por outras etapas como garantia de que
nada muda ali). Ver proposta em §11.

## 10. Testes existentes relevantes

50 arquivos em `API/tests/`. Os mais relevantes para esta frente:

- `test_captions.py` — legenda contínua (agrupamento, `.ass`, escala de fonte, caixa).
- `test_highlight_ass.py`, `test_highlight_planner.py` — legenda de destaque.
- `test_broll_catalog.py` — catálogo de presets, validação de parâmetros, prévias.
- `test_broll_transitions.py` — `_plan_cutaways`/`apply_cutaways`/`validate_cutaways`.
- `test_broll_planner.py`, `test_broll_source.py` — planejamento e busca/preparo de
  B-roll.
- `test_render.py` — render completo (letterbox, reenquadrar, cor, áudio).
- `test_incremental_render.py` — hash por segmento, reaproveitamento, invalidação.
- `test_editing_document.py`, `test_editing_history.py`, `test_editing_replace.py`,
  `test_editing_preview.py`, `test_editing_chat*.py`,
  `test_editing_robustness_api.py` — documento de edição, histórico, substituição,
  prévia e chat.
- `test_zoom.py`, `test_reframe.py`, `test_face.py` — zoom e rastreio.
- `test_fluxo_integration.py`, `test_pipeline_integration.py` — fluxo completo com
  vídeos reais (`-m integration`).

Rodar tudo: `cd API && uv run pytest -q` (rápidos) e `uv run pytest -q -m integration`
(lentos, precisam de `samples/`, GPU ou LLM pago). Estado no momento desta auditoria:
**619 testes rápidos passam**, Ruff limpo, `python -m src.doctor` sem itens faltando.

## 11. Riscos de regressão

1. **Dois geradores de `.ass` para unificar (ou envolver) sem quebrar nenhum.** Qualquer
   mudança no contrato de `CaptionStyle`/`HighlightStyle` ou nas funções privadas
   compartilhadas (`_cor_ass`, `_tempo_ass`, `libass_scale`, todas em `captions.py` e
   importadas por `highlight_captions/ass_builder.py`) afeta os dois motores ao mesmo
   tempo.
2. **A exclusividade `legendas_continuas`/`legendas_destaque` está validada em dois
   lugares** (`Project` e `PipelineOptions`) — uma nova modalidade de legenda (ex.: um
   terceiro modo) precisa atualizar os dois validadores e o seletor único do frontend.
3. **`_tempo_ass` arredonda para baixo de propósito** para as legendas nunca "vazarem"
   1 frame para o clipe seguinte na emenda. Uma nova animação com tags de tempo
   próprias (ex.: `\t(...)` do libass) precisa preservar essa disciplina.
4. **A emenda entre clipes (`TimeMap.seams()`) é tratada como fronteira dura em vários
   lugares independentes**: `_plan_cutaways` (B-roll), a validação de destaques
   (`highlight_captions/planner.py`, não lida em detalhe nesta auditoria mas citada no
   CLAUDE.md como "sem cruzar clipes nem trechos"), e a suposição geral de sincronia
   A/V do render (`_audio_args`/`_concat`). Um Transition Engine genérico que queira
   atravessar essa fronteira precisa revisar todos esses pontos, não só adicionar uma
   função nova.
5. **`xfade` não é checado no `doctor`** — hoje isso é aceitável porque o fallback para
   `hard_cut` já existe e avisa (`_plan_cutaways`, linha 130). Uma biblioteca maior de
   efeitos (Etapa 6/7 do plano) deveria manter esse padrão de fallback + aviso, não
   assumir que o efeito está disponível.
6. **`_second_pass` roda por `cwd`** (nomes de arquivo relativos, não caminhos
   absolutos) por causa de como o FFmpeg no Windows lida com `:` em filtros. Qualquer
   novo asset (fonte, textura, LUT) usado num filtro complexo precisa ser copiado para
   essa mesma pasta de trabalho, senão quebra silenciosamente só no Windows.
7. **O hash do render incremental já é grande e sensível** (`_hash_segment`); adicionar
   uma nova dimensão de configuração (estilo de legenda com mais campos, efeito de
   transição novo) é seguro *se* ela entrar nesse dicionário — esquecer de incluir um
   campo novo faria o cache **não invalidar** quando deveria (bug silencioso, o pior
   tipo aqui).
8. **Performance**: `_second_pass` já é um único `filter_complex` com N overlays mais
   `ass=`; animações de legenda mais elaboradas ou muitas transições longas no mesmo
   vídeo podem tornar esse grafo grande. Vale medir antes de generalizar demais (o
   plano já pede isso na Etapa 10).

## 12. Pontos de integração propostos (sem implementar ainda)

Estas são sugestões para a Etapa 2 (Caption) e Etapa 5 (Transition) avaliarem — não é
código, é onde plugar:

- **Caption Style Engine**: um registry (`dict[str, ...]`) que mapeia um nome de preset
  para uma função `build(palavras_por_clipe, params) -> texto .ass`, com o preset
  `"legacy"`/`"default"` sendo literalmente `captions.build_ass` hoje (sem mudar uma
  linha dele). O ponto de chamada (`pipeline.make_captions`, `render.py:379`) trocaria
  `write_captions(..., options.estilo_legenda...)` por algo como
  `render_captions(preset, params, ...)`. O gerador de destaque poderia, com o tempo,
  virar só mais um preset desse registry (ele já é estruturalmente parecido: agrupa
  palavras, gera eventos, escreve cabeçalho). Como o cache incremental já invalida pelo
  conteúdo do `.ass` (§8), isso não exige mudanças em `segments.py`/
  `incremental_render.py` **desde que continue existindo um `.ass` só por render**.
- **Transition Engine (fundação, Etapa 5)**: generalizar `Cutaway`/`_plan_cutaways`
  para um conceito de "substituição de trecho" que aceite tanto um arquivo de B-roll
  quanto — se a decisão do §9 for "sim, também nas emendas" — o próprio clipe
  seguinte como fonte de vídeo do lado B do `xfade`. Isso muda a suposição "nunca
  atravessa emenda" em pelo menos três lugares (§11.4), então é exatamente o tipo de
  decisão que a regra do plano pede para **parar e perguntar antes**, não decidir
  sozinho durante a Etapa 5.
- **Contrato para o LLM (Etapa 8)**: o padrão já existe e está provado em produção —
  schema Pydantic + prompt em `.md` + `run_structured` (`src/llm/client.py`), com
  validação em código separada da decisão do LLM (`validate_cutaways`,
  `validate_highlights` equivalente). Um novo `PlanoTransicoes`/`PlanoLegendas`
  seguiria o mesmo padrão dos já existentes em `src/llm/schemas.py`.

## Riscos/observações fora do escopo original do plano

- O plano cita "verificar interação com `warpPerspective`" — não existe no projeto;
  só `warpAffine` (translação + escala, nunca rotação/perspectiva). Ajustado no texto
  desta auditoria (§3, §6).
- O plano assume que pode haver "já alguma implementação de transições" — há, e é mais
  madura do que o texto do plano supõe (7 presets com catálogo, prévias, validação de
  parâmetros e testes), mas é **inteiramente escopada a B-roll**. Isso muda o ponto de
  partida real da Etapa 5–7: não é "criar do zero", é "decidir se generaliza o que
  existe ou cria um segundo mecanismo paralelo" — voltando ao §9.
