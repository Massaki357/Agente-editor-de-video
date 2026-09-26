# Novas Ferramentas: Etapas de Desenvolvimento

## Como usar este arquivo com o Claude Code

1. Coloque este arquivo na raiz do projeto.
2. Peça uma etapa por vez: *"Leia o novas-etapas.md e implemente a Etapa N da Parte X."*
3. Rode os testes e faça commit antes de pedir a próxima etapa.

Este arquivo cobre seis frentes de trabalho:
- **Parte 1 — Otimizador de Áudio:** limpa ruído de fundo e normaliza volume.
- **Parte 2 — Estabilizador de Vídeo:** remove tremedeira de câmera na mão.
- **Parte 3 — Cutaways de B-roll:** troca a tela inteira por vídeos relacionados ao assunto, com transição, enquanto a voz continua.
- **Parte 4 — Legendas de Destaque:** mostra só frases de impacto na tela, em vez de legenda o tempo todo.
- **Parte 5 — Edição Pós-Render e Chat de Ajustes:** editar, substituir elementos e pedir ajustes por chat depois do vídeo já gerado, sem recomeçar do zero.
- **Parte 6 — Transições adicionais de B-roll:** amplia os efeitos e a escolha na interface.

Cada parte pode ser desenvolvida e usada separadamente, mas todas se encaixam no mesmo pipeline do editor de vídeos (`etapas.md`).

---

# Parte 1 — Otimizador de Áudio

## Visão geral

Módulo que recebe um áudio (ou o áudio extraído de um vídeo) e devolve uma versão limpa: sem ruído de fundo, sem chiado, com volume normalizado. Roda 100% local, sem LLM.

**Onde entra no projeto do editor de vídeos:** como um passo opcional sobre o **áudio do vídeo final** (a transcrição usa sempre o áudio original — ver a medição na Etapa 3). Também funciona como ferramenta independente (`python -m src.audio.optimize entrada.mp3 saida.mp3`).

---

## Decisões fixas

| Tema | Decisão |
|---|---|
| Redução de ruído | **DeepFilterNet** (rede neural, roda em CPU, resultado bem acima de filtros clássicos), pelo **binário oficial** baixado uma vez para o `CACHE_DIR/models/` — o pacote `deepfilternet` do PyPI só tem wheel até o Python 3.11 e, no 3.12 deste projeto, tentaria compilar Rust (decidido com o usuário na Etapa 0) |
| Fallback leve | RNNoise do FFmpeg (`arnndn`), `noisereduce` (spectral gating) ou filtros FFmpeg (`afftdn`, `highpass`, `lowpass`), caso o DeepFilterNet não esteja disponível |
| Normalização de volume | `loudnorm` do FFmpeg (padrão EBU R128, 2 passadas) |
| Remoção de DC offset / rumble | `highpass=f=80` antes do resto da cadeia |
| Formato de trabalho | WAV 48 kHz **mono** internamente (decidido na Etapa 1): é o que o Whisper usa e o que os motores de denoise esperam; a Etapa 3 decide o que vai no vídeo final, onde o áudio original (estéreo) continua sendo o padrão. Conversão de entrada/saída via FFmpeg |
| Cache | Por hash do arquivo de entrada + versão da cadeia de processamento |
| Integração | Módulo isolado em `src/audio/`, chamado pelo pipeline de vídeo e utilizável sozinho via CLI |

### Estrutura de pastas alvo

```
src/audio/                 (dentro de API/)
├── __init__.py
├── deepfilter.py     # binário oficial do DeepFilterNet: baixar e rodar   (Etapa 0 ✔)
├── metrics.py         # piso de ruído, nível de fala, SNR e conversão WAV  (Etapa 0 ✔)
├── optimize.py      # cadeia highpass -> denoise -> loudnorm, com cache      (Etapa 1 ✔)
├── denoise.py        # DeepFilterNet -> noisereduce -> afftdn                (Etapa 1 ✔)
├── loudness.py        # medição e normalização (loudnorm 2 passadas)          (Etapa 1 ✔)
│                     `cached_audio` guarda um WAV por clipe (Etapa 3 ✔)
└── cli.py            # `python -m src.audio.optimize`, barra e antes/depois  (Etapa 2 ✔)
```

---

## Etapa 0: Setup e prova de conceito

**Objetivo:** confirmar que o DeepFilterNet roda no ambiente do usuário.

**Tarefas**
- Adicionar `deepfilternet` (pacote `df`) e `noisereduce` ao `pyproject.toml`.
- Script simples: extrai o áudio de um arquivo de amostra, roda o DeepFilterNet e salva o resultado, sem integração ainda.
- `python -m src.doctor` (do projeto principal) passa a checar se o `df`/DeepFilterNet importa corretamente.

**Critérios de aceite**
- Rodar o script num áudio de teste com ruído de fundo (ventilador, ar-condicionado, etc.) produz um arquivo audivelmente mais limpo.
- Se o DeepFilterNet falhar ao importar (ex.: sem suporte na plataforma), o doctor avisa claramente em vez de quebrar em silêncio.

---

## Etapa 1: Cadeia básica de limpeza

**Objetivo:** função única que limpa um arquivo de áudio.

**Tarefas**
- `audio/optimize.py`: `optimize_audio(caminho_entrada, caminho_saida, aggressiveness=0.5)`.
  - Passo 1: `highpass=f=80` (remove rumble/DC offset) via FFmpeg.
  - Passo 2: `audio/denoise.py` aplica o DeepFilterNet; se indisponível, cai para `noisereduce`; se nenhum dos dois estiver disponível, cai para `afftdn` do FFmpeg.
  - Passo 3: `audio/loudness.py` normaliza com `loudnorm` em duas passadas (mede e depois aplica os valores medidos, para não estourar picos).
- Parâmetro `aggressiveness` controla o quanto o denoise é aplicado (mix entre sinal original e processado), para evitar voz robotizada em áudios pouco ruidosos. **Escala calibrada na Etapa 1** (medida com fala real): 0 não mexe, 0,5 tira 20 dB de ruído e 1 tira 100 dB; a atenuação pedida aparece quase exata no piso de ruído e o nível da voz não muda.
- Cache por hash do arquivo + versão da cadeia (mudar um parâmetro invalida o cache).

**Critérios de aceite**
- Áudio de teste com ruído perceptível sai limpo e com volume consistente (medir LUFS antes/depois).
- Áudio já limpo, passado pela cadeia, não perde qualidade perceptível nem fica "robótico" no `aggressiveness` padrão.
- Segunda execução no mesmo arquivo usa o cache.

---

## Etapa 2: CLI standalone

**Objetivo:** usar o otimizador sem precisar do resto do projeto.

**Tarefas**
- `audio/cli.py`: `python -m src.audio.optimize entrada.(mp3|wav|mp4) saida.wav [--aggressiveness 0.5] [--format mp3|wav]`, mais `--lufs`, `--motor`, `--sem-normalizar`, `--sem-cache` e `-q`.
- Aceita vídeo como entrada (extrai o áudio automaticamente) ou áudio puro.
- Barra de progresso simples no terminal (uma linha por etapa quando a saída é redirecionada) e uma tabela antes/depois no fim: fala, ruído, SNR e volume.
- A prova de conceito da Etapa 0 (`audio/poc.py`) saiu: a CLI faz o mesmo e mais.

**Critérios de aceite**
- Funciona apontando tanto para um `.mp3` quanto para um `.mp4`.
- Mensagens de erro claras quando o arquivo não existe ou o formato não é suportado.

---

## Etapa 3: Integração no pipeline do editor de vídeos

**Objetivo:** usar o otimizador dentro do fluxo principal, sem obrigar todo mundo a usá-lo.

> **Medição mudou o desenho desta etapa (23/09/2026).** A premissa era "áudio limpo
> transcreve melhor". Medindo com fala real + chiado e comparando com a transcrição do
> áudio sem ruído (similaridade de palavras), limpar **piorou** o reconhecimento nas três
> amostras: 0,568 → 0,528 · 0,685 → 0,575 · 0,743 → 0,715. O Whisper já é treinado com
> ruído e os artefatos da limpeza atrapalham. Com o aval do usuário, a opção passou a
> valer **só para o áudio do vídeo final**; a transcrição usa sempre o áudio original.

**Tarefas**
- Opção `limpar_audio: bool` no `PipelineOptions` e na UI (Etapa 10 do `etapas.md`).
- Quando ligada, o **áudio do vídeo final** sai limpo e normalizado; a transcrição, os cortes e as legendas continuam usando o áudio original.
- Ajustes finos (`parametros_audio`: intensidade, volume alvo, normalizar) na mesma seção da UI.
- Cache isolado por clipe (`optimize.cached_audio`, um WAV por clipe + parâmetros), reaproveitado entre execuções.

**Critérios de aceite**
- Ligar/desligar a opção não quebra as etapas seguintes do pipeline.
- Com a opção ligada, o vídeo final sai com o ruído reduzido, no volume alvo, e com áudio e vídeo na mesma duração (nada desalinha).

---

## Etapa 4: Ajuste fino e testes

**Tarefas**
- Testes automatizados comparando LUFS e SNR antes/depois em três ruídos diferentes (`tests/test_audio_qualidade.py`, com a fala real de `samples/`). Medido: chiado +18,0 dB de SNR, ambiente +18,9 dB e volume sempre em −15,8/−15,9 LUFS (alvo −16). O eco tem teste próprio, que **prova a limitação**: a reflexão medida por autocorrelação cai só de 0,30 para 0,27 (o SNR não serve ali, porque o DeepFilterNet zera os trechos entre as palavras e infla o número).
- Padrões no `.env`: `AUDIO_AGGRESSIVENESS` (0,5) e `AUDIO_TARGET_LUFS` (−16), lidos por `AudioParams.do_env()` e usados pela CLI e pelo pipeline.
- `README.md` com a seção "Limpando o áudio" (uso no editor e pela CLI, com a tabela das medições).
- Robustez: falha na limpeza de um clipe não derruba o render — aquele clipe fica com o áudio original e o job avisa; o tempo da limpeza aparece como `tempos["limpeza do áudio"]`.

**Critérios de aceite**
- Testes de SNR/LUFS passam com a margem definida (`GANHO_MINIMO` é por motor: 12/10 dB no DeepFilterNet, 4/6 no noisereduce, 12/3 no afftdn). O eco tem teste próprio, que exige que a reflexão **continue lá** — a cadeia tira ruído, não eco.
- `README.md` cobre os dois modos de uso (standalone e integrado).

---

## Checklist Parte 1

- [x] Etapa 0: Setup e prova de conceito
- [x] Etapa 1: Cadeia básica de limpeza
- [x] Etapa 2: CLI standalone
- [x] Etapa 3: Integração no pipeline do editor de vídeos
- [x] Etapa 4: Ajuste fino e testes

---

# Parte 2 — Estabilizador de Vídeo

## Visão geral

Módulo que recebe um vídeo gravado com a câmera tremendo na mão e devolve uma versão estabilizada, sem o "chacoalhado". Roda 100% local, sem LLM.

**Onde entra no projeto do editor de vídeos:** como um passo opcional **antes** do rastreio de rosto e do reenquadramento vertical (Etapas 5 e 6 do `etapas.md`) — estabilizar antes ajuda o rastreio a ficar mais suave, já que o rosto para de saltar de posição por causa do tremor da câmera. Também funciona como ferramenta independente (`python -m src.video.stabilize entrada.mp4 saida.mp4`).

## Decisões fixas

| Tema | Decisão |
|---|---|
| Método | **Estabilização por análise de trajetória em duas passadas**: (1) rastreia o movimento da câmera entre frames com pontos de referência (optical flow), (2) suaviza a trajetória e recorta cada frame para compensar o tremor |
| Biblioteca base | OpenCV (`cv2.calcOpticalFlowPyrLK` + `cv2.VideoWriter`), sem depender de serviço externo |
| Alternativa mais simples | Filtro `vidstab` do FFmpeg (`vidstabdetect` + `vidstabtransform`) como primeira opção — mais rápido de integrar e já maduro; só partir para OpenCV customizado se o resultado do `vidstab` não for suficiente |
| Corte de borda | Zoom leve (crop) automático para esconder as bordas pretas que aparecem ao compensar o tremor; percentual configurável |
| Nível de suavização | Configurável (leve/médio/forte), porque suavização forte demais cria um efeito "flutuante" artificial |
| Cache | Por hash do arquivo de entrada + parâmetros usados |
| Integração | Módulo isolado em `src/video/stabilize.py`, chamado pelo pipeline principal e utilizável sozinho via CLI |

### Estrutura de pastas alvo

```
src/video/
├── __init__.py
├── stabilize.py       # orquestra: vidstab (padrão) ou fallback OpenCV
└── cli.py             # ponto de entrada `python -m src.video.stabilize`
```

## Etapa 0: Setup e prova de conceito

**Objetivo:** confirmar que o `vidstab` está disponível no FFmpeg do usuário.

**Tarefas**
- `python -m src.doctor` passa a checar se o FFmpeg instalado tem os filtros `vidstabdetect`/`vidstabtransform` compilados (nem toda distribuição do FFmpeg vem com eles).
- Se não tiver, o doctor avisa e aponta a alternativa (reinstalar FFmpeg com `--enable-libvidstab`, ou usar o fallback OpenCV da Etapa 3).
- Script simples: roda as duas passadas do `vidstab` num vídeo de amostra tremido e salva o resultado.

**Critérios de aceite**
- Vídeo de teste gravado na mão, com tremor visível, sai perceptivelmente mais estável.
- Ambiente sem `vidstab` recebe aviso claro em vez de erro obscuro do FFmpeg.

## Etapa 1: Cadeia básica de estabilização (vidstab)

**Objetivo:** função única que estabiliza um vídeo.

**Tarefas**
- `video/stabilize.py`: `stabilize_video(caminho_entrada, caminho_saida, smoothing="medio", crop_percent=None)`.
  - Passada 1: `vidstabdetect` gera o arquivo de trajetória (`transforms.trf`).
  - Passada 2: `vidstabtransform` aplica a compensação, com `smoothing` mapeado para o parâmetro `smoothing` do filtro (leve/médio/forte → valores numéricos calibrados) e `zoom` para cobrir as bordas.
  - Se `crop_percent` não for informado, calcular automaticamente o menor crop que esconde a borda preta no vídeo de amostra padrão (ajustável depois).
- Preservar o áudio original sem reprocessar.
- Cache por hash do arquivo + parâmetros (mudar o nível de suavização invalida o cache).

**Critérios de aceite**
- Vídeos com tremor leve, médio e forte (3 amostras) saem estáveis nos três níveis de `smoothing`, sem bordas pretas visíveis.
- Áudio permanece sincronizado após o processamento.
- Segunda execução com os mesmos parâmetros usa o cache.

## Etapa 2: CLI standalone

**Objetivo:** usar o estabilizador sem precisar do resto do projeto.

**Tarefas**
- `video/cli.py`: `python -m src.video.stabilize entrada.mp4 saida.mp4 [--smoothing leve|medio|forte] [--crop 5]`.
- Barra de progresso (as duas passadas do `vidstab` podem demorar em vídeos longos).

**Critérios de aceite**
- Funciona de ponta a ponta num vídeo de alguns minutos sem travar.
- Mensagens de erro claras quando o `vidstab` não está disponível ou o arquivo de entrada é inválido.

## Etapa 3: Fallback com OpenCV (opcional)

**Objetivo:** cobrir ambientes sem `vidstab` compilado no FFmpeg.

**Tarefas**
- `video/stabilize.py` ganha um segundo caminho: rastrear pontos de referência frame a frame (`cv2.goodFeaturesToTrack` + `cv2.calcOpticalFlowPyrLK`), estimar a transformação entre frames (`cv2.estimateAffinePartial2D`), suavizar a trajetória acumulada (média móvel) e aplicar `cv2.warpAffine` com crop de borda.
- Seleção automática: usa `vidstab` se disponível, cai para o fallback OpenCV se não.
- Deixar claro nos logs qual dos dois métodos foi usado.

**Critérios de aceite**
- No mesmo vídeo de teste, o fallback OpenCV também remove o tremor perceptivelmente, mesmo que com qualidade um pouco diferente do `vidstab`.
- A troca entre os dois métodos é transparente para quem chama `stabilize_video`.

## Etapa 4: Integração no pipeline do editor de vídeos

**Objetivo:** usar a estabilização dentro do fluxo principal.

**Tarefas**
- Opção `estabilizar: bool` (com nível de suavização) no `project.json` e na UI (Etapa 10 do `etapas.md`).
- Quando ligada, roda **por clipe**, antes do rastreio de rosto (Etapa 5 do `etapas.md`) — um vídeo já estável facilita o rastreio e evita zoom/crop reagindo ao tremor da câmera.
- Cache isolado por clipe, reaproveitado entre execuções.

**Critérios de aceite**
- Ligar/desligar a opção não quebra as etapas seguintes do pipeline.
- Num clipe tremido, o rastreio de rosto (Etapa 5) fica visivelmente mais suave com a estabilização ligada do que desligada.

## Etapa 5: Ajuste fino e testes

**Tarefas**
- Testes automatizados medindo a redução de tremor (ex.: variância do deslocamento entre frames, antes/depois) em 2-3 vídeos de amostra com níveis de tremor diferentes.
- Expor no `.env`: `STABILIZE_SMOOTHING` (padrão `medio`), `STABILIZE_CROP_PERCENT`.
- Documentar no `README.md` os dois métodos (vidstab e fallback OpenCV) e como usar a CLI.

**Critérios de aceite**
- Testes de redução de tremor passam com uma margem definida nos vídeos de amostra.
- `README.md` cobre os dois modos de uso (standalone e integrado) e explica quando o fallback entra em ação.

---

## Checklist Parte 2

- [x] Etapa 0: Setup e prova de conceito
- [x] Etapa 1: Cadeia básica de estabilização (vidstab)
- [x] Etapa 2: CLI standalone
- [x] Etapa 3: Fallback com OpenCV
- [x] Etapa 4: Integração no pipeline do editor de vídeos
- [x] Etapa 5: Ajuste fino e testes

---

# Parte 3 — Cutaways de B-roll

## Visão geral

Enquanto você continua falando, a tela corta para um vídeo relacionado ao assunto (não uma imagem parada num canto, a tela inteira) e depois volta para você, com uma transição entre os dois. É o efeito de "documentário"/vídeo de criador: a voz nunca para, só o que aparece na tela muda.

**Diferença em relação às imagens da Etapa 8 do `etapas.md`:** lá, a imagem é um elemento pequeno sobreposto (canto, lateral), sem cobrir o rosto. Aqui, o cutaway ocupa a tela inteira por alguns segundos e depois volta ao vídeo original, então exige planejamento por **trecho de fala** (frase/período), não por palavra isolada, e uma transição de entrada/saída.

**Onde entra no pipeline principal:** depois dos cortes de silêncio e erros de fala (Etapas 3-4 do `etapas.md`) e depois de já ter a transcrição global com tempos finais, na mesma etapa em que as imagens são planejadas (Etapa 8), mas como um recurso adicional: cada trecho pode virar imagem sobreposta OU cutaway de vídeo, dependendo do que o LLM decidir.

## Decisões fixas

| Tema | Decisão |
|---|---|
| Unidade de planejamento | **Trecho de fala** (frase ou período semântico com início/fim), não palavra isolada |
| Fonte dos clipes de B-roll | Pexels Videos (API própria de vídeo) e Pixabay Videos como fallback |
| Transição padrão | Corte seco (hard cut) — é o que mais se vê nesse estilo e é o mais barato de renderizar. Crossfade, deslizamento e varredura (`xfade` do FFmpeg) como opções configuráveis |
| Áudio | **Nunca corta.** O áudio original (voz) segue contínuo por baixo do cutaway; só a imagem muda |
| Duração de cada cutaway | Entre 1,5 s e o tamanho do trecho de fala correspondente, limitado a um teto configurável (padrão 4 s) |
| Densidade | No máximo 1 cutaway a cada ~8-10 s de fala, para não virar um vídeo picado demais |
| Retorno à câmera | Sempre volta para o vídeo original ao fim do trecho, nunca emenda direto num segundo cutaway sem mostrar a pessoa entre eles |
| Normalização dos clipes de B-roll | Mesma resolução/fps/formato do vídeo final (1080x1920, 30fps); cortar/centralizar o clipe de B-roll para caber no vertical |

### Estrutura de pastas alvo

```
src/broll/
├── __init__.py
├── planner.py     # schema Pydantic + prompt para o LLM planejar os cutaways
├── source.py       # busca e download nos provedores (Pexels/Pixabay Videos)
├── transitions.py   # hard cut e crossfade via FFmpeg
└── cli.py           # teste isolado com um vídeo + transcrição de amostra
```

## Etapa 0: Planejamento com o LLM

**Objetivo:** decidir *quando* e *sobre o quê* cada cutaway aparece.

**Tarefas**
- Estender o schema do plano criativo (junto ao de imagens/zooms, Etapa 8 do `etapas.md`) com `broll: [{trecho_inicio_palavra, trecho_fim_palavra, query, duracao_max, motivo}]`.
- Prompt em `llm/prompts/plano_broll.md`: recebe a transcrição global (frases já segmentadas) e escolhe só trechos onde um vídeo relacionado agrega (ex.: menção a um lugar, objeto, processo, dado), nunca no meio de uma frase.
- Regra explícita no prompt: cada trecho vira **imagem sobreposta OU cutaway de vídeo, nunca os dois ao mesmo tempo** no mesmo intervalo.
- Validação em código: trechos não se sobrepõem entre si nem com outro cutaway, respeitam a densidade máxima, têm duração dentro do limite.

**Critérios de aceite**
- Rodando num roteiro de teste, os cutaways caem em trechos que fazem sentido (verificação manual) e nunca cortam no meio de uma palavra.
- Densidade e duração sempre dentro dos limites configurados, mesmo se o LLM sugerir mais.

## Etapa 1: Busca e preparo dos clipes de B-roll

**Objetivo:** baixar e normalizar o vídeo de cada cutaway.

**Tarefas**
- `source.py`: busca por `query` no Pexels Videos, fallback Pixabay Videos, baixando a maior resolução disponível até um teto (para não baixar 4K à toa).
- Cortar o clipe de B-roll no tamanho da `duracao_max`, cortar/centralizar para 1080x1920 e converter para 30fps.
- Cache local por `query` + duração, para não rebaixar o mesmo clipe em execuções futuras.
- Fallback quando a busca não retorna nada relevante: descartar o cutaway daquele trecho (cair para imagem sobreposta ou nada), nunca travar o pipeline.

**Critérios de aceite**
- Clipes baixados já saem no formato final, prontos para entrar na timeline sem reprocessamento extra.
- Busca sem resultado não quebra a renderização.

## Etapa 2: Transições e montagem na timeline

**Objetivo:** encaixar os cutaways no vídeo final.

**Tarefas**
- `transitions.py`: aplicar hard cut (concatenação direta) ou efeito `xfade` do FFmpeg (~0,2-0,3 s): crossfade, deslizamento ou varredura, conforme configuração.
- Inserir os cutaways na timeline global **depois** do reenquadramento vertical e dos zooms (Etapas 6 e 9 do `etapas.md`), já que eles substituem o quadro inteiro nesses intervalos — não precisam de crop dinâmico nem de rastreio de rosto.
- Manter o áudio original (voz) sem cortes durante todo o processo; só a trilha de vídeo é trocada nos intervalos de cutaway.
- Cutaways nunca atravessam a emenda entre clipes originais do projeto.

**Critérios de aceite**
- No vídeo final, o áudio da voz é contínuo do início ao fim, sem falha nem salto, mesmo durante os cutaways.
- A transição (corte ou crossfade) fica suave, sem quadro preto ou travado entre um cutaway e outro.

## Etapa 3: Preview e aprovação

**Objetivo:** revisar antes do render final, como já existe para as imagens (Etapa 8 do `etapas.md`).

**Tarefas**
- Modo preview: lista de cutaways sugeridos (trecho de fala, clipe escolhido, duração) para o usuário aprovar, trocar ou remover.
- Trocar um cutaway no preview e re-renderizar não chama o LLM de novo (reaproveita o plano já validado).

**Critérios de aceite**
- Remover um cutaway no preview reflete corretamente no render final (o trecho volta a mostrar só a pessoa falando).

## Etapa 4: Integração na UI

**Tarefas**
- Opção `broll: bool` no `project.json` e na UI (Etapa 10 do `etapas.md`), com controle de densidade máxima e tipo de transição padrão.
- Lista de aprovação de cutaways na mesma tela de preview das imagens.

**Critérios de aceite**
- Ligar/desligar a opção não quebra as etapas seguintes do pipeline.
- Vídeo de teste completo (3 clipes) com imagens, zooms e cutaways juntos, sem sobreposição entre os recursos.

---

## Checklist Parte 3

- [x] Etapa 0: Planejamento com o LLM
- [x] Etapa 1: Busca e preparo dos clipes de B-roll
- [x] Etapa 2: Transições e montagem na timeline
- [ ] Etapa 3: Preview e aprovação
- [ ] Etapa 4: Integração na UI

As implementações e verificações automáticas das Etapas 3 e 4 estão prontas. Os
checklists aguardam a conferência visual registrada em `testes-pendentes.md`
(P3-C3: pertinência dos vídeos de café; P3-C4: interface e vídeo de três clipes).

---

# Parte 4 — Legendas de Destaque

## Visão geral

Em vez de legenda o vídeo inteiro (Etapa 7 do `etapas.md`), só **trechos de impacto com até cinco palavras** ganham texto na tela: o texto vai aparecendo enquanto a pessoa fala e continua visível por um instante depois que ela termina, antes de sumir. É o efeito de ênfase que se vê em vídeos de criador, usado com moderação, não em toda fala.

**Incompatibilidade com a Etapa 7:** as duas legendam o vídeo, mas com lógicas opostas (contínua vs. só destaques) e estilos visuais diferentes. Usar as duas juntas polui a tela e confunde o espectador. **As duas opções são mutuamente exclusivas: o projeto só pode ter uma ativa por vez, nunca as duas.**

**Onde entra no pipeline principal:** mesmo ponto da Etapa 7 do `etapas.md` (depois de ter os tempos finais das palavras), mas como alternativa a ela, não como adição.

## Decisões fixas

| Tema | Decisão |
|---|---|
| Unidade de planejamento | **Trecho literal de até cinco palavras consecutivas** dentro de uma frase de impacto, sem reescrever a transcrição |
| Exclusividade | Validação em código: `legendas_continuas` e `legendas_destaque` nunca podem ser `true` ao mesmo tempo no `project.json`. UI (Etapa 10 do `etapas.md`) usa um único seletor (nenhuma / contínua / destaque), não duas caixas independentes |
| Aparição do texto | Revelação progressiva palavra a palavra, acompanhando a fala (efeito karaokê), não a frase inteira de uma vez |
| Permanência após a fala | Frase completa fica na tela por um tempo configurável depois que a pessoa termina de falar (padrão 1,2 s), depois some com fade |
| Estilo visual | Fonte menor (72 px na base 1080x1920), até duas linhas e posição estável escolhida entre áreas livres; usa as caixas de rosto quando disponíveis e prefere o alto do quadro sem cobrir a pessoa |
| Formato de geração | Mesmo mecanismo da Etapa 7 (arquivo `.ass` queimado com o filtro `ass=`), com um estilo e uma lógica de tempo próprios |
| Densidade | Poucas por vídeo — o LLM escolhe só as frases realmente marcantes, não frase a frase |

### Estrutura de pastas alvo

```
src/highlight_captions/
├── __init__.py
├── planner.py     # schema Pydantic + prompt para o LLM escolher as frases de impacto
└── ass_builder.py  # gera o .ass com revelação progressiva + permanência
```

## Etapa 0: Planejamento com o LLM

**Objetivo:** escolher quais frases merecem destaque.

**Tarefas**
- Novo schema no plano criativo: `destaques: [{trecho_inicio_palavra, trecho_fim_palavra, texto, motivo}]`.
- Prompt em `llm/prompts/plano_destaques.md`: recebe as frases e os índices das palavras e escolhe trechos curtos com carga (dado forte, virada de argumento, frase de efeito), evitando trechos consecutivos.
- Validação em código: até cinco palavras consecutivas da mesma frase; trechos não se sobrepõem, respeitam um espaçamento mínimo configurável e o texto bate com a transcrição original.

**Critérios de aceite**
- Num roteiro de teste, os destaques escolhidos são, na checagem manual, os trechos mais fortes do texto, não escolhas aleatórias.
- Nenhum destaque sobrepõe outro nem extrapola os limites do trecho de fala original.

## Etapa 1: Geração do `.ass` com revelação e permanência

**Objetivo:** montar a legenda de destaque com o timing certo.

**Tarefas**
- `ass_builder.py`: para cada destaque, gerar os eventos do `.ass` com o texto revelado palavra a palavra no tempo exato de cada palavra (igual ao karaokê da Etapa 7), e manter a frase completa na tela por `duracao_permanencia` depois da última palavra, com fade de saída.
- Estilo próprio (fonte menor, cor/contorno configuráveis e posição que evita caixas ocupadas), separado do estilo da legenda contínua.
- Garantir que o destaque nunca atravessa a emenda entre clipes.

**Critérios de aceite**
- No vídeo final, o texto acompanha a fala palavra a palavra e permanece visível pelo tempo configurado depois da frase terminar.
- Nenhum destaque é cortado pela emenda entre clipes.

## Etapa 2: Exclusividade e integração na UI

**Objetivo:** impedir o uso simultâneo com a legenda contínua.

**Tarefas**
- Validação no carregamento/salvamento do `project.json`: rejeitar (com mensagem clara) se `legendas_continuas` e `legendas_destaque` estiverem ativas juntas.
- Na UI (Etapa 10 do `etapas.md`), trocar as duas caixas de legenda por um único seletor: **Nenhuma / Legenda contínua / Legendas de destaque**.
- Ajustes expostos: tempo de permanência, tamanho da fonte, cor.

**Critérios de aceite**
- Não é possível gerar (nem salvar) um projeto com as duas opções de legenda ativas ao mesmo tempo.
- Trocar entre os três modos no seletor re-renderiza corretamente sem sobras da opção anterior.

---

## Checklist Parte 4

- [x] Etapa 0: Planejamento com o LLM
- [ ] Etapa 1: Geração do `.ass` com revelação e permanência
- [ ] Etapa 2: Exclusividade e integração na UI

A implementação e os testes automatizados da Etapa 1 estão prontos. A aprovação do
checklist aguarda a conferência perceptiva de `output/parte4_etapa1_fala_real_curta.mp4`
pela pessoa usuária. A Etapa 2 também foi implementada e passou nos testes automatizados;
seu critério de troca de modos no vídeo final aguarda avaliação visual pela pessoa usuária.
(P4-C1 em `testes-pendentes.md`).

---

# Parte 5 — Edição Pós-Render e Chat de Ajustes

## Visão geral

Depois que o vídeo é gerado, o usuário precisa poder:
1. **Substituir** um vídeo/imagem específico que o agente escolheu (ex.: trocar a imagem da "cesta de frutas" por outra), sem refazer transcrição, cortes, rastreio de rosto nem o plano inteiro.
2. **Ajustar** o vídeo pedindo por **chat**, em linguagem natural ("tira o zoom da parte 2", "troca a legenda de destaque da frase X", "esse cutaway ficou longo demais, diminui"), sem editar JSON na mão.

Essa é a peça mais complexa do projeto, porque toca em todas as outras partes (etapas.md e Partes 1-4 deste arquivo). Por isso ela só começa depois que pelo menos as Etapas 0-9 do `etapas.md` estiverem prontas — precisa ter o que editar antes de editar.

**Princípio central:** o projeto para de ser "gerar e esquecer" e passa a ser um **documento vivo**. O `project.json` (Etapa 1 do `etapas.md`) precisa guardar *toda* decisão do pipeline (não só a lista de clipes), para que qualquer elemento possa ser trocado e só aquele trecho seja renderizado de novo — nunca o vídeo inteiro.

## Decisões fixas

| Tema | Decisão |
|---|---|
| Fonte da verdade | `project.json` vira um documento único e versionado com **todas** as decisões: cortes, crops de rosto, zooms, imagens, cutaways de b-roll, legendas, destaques — cada elemento com um `id` estável |
| Granularidade de edição | Toda edição (manual ou por chat) altera **um ou mais elementos pelo `id`**, nunca o JSON inteiro. Isso é o que permite re-renderizar só o necessário |
| Render incremental | O render final é montado por **segmentos independentes**; trocar um elemento invalida só o cache dos segmentos que o contêm, não o vídeo inteiro |
| Histórico | Cada edição gera uma nova versão do projeto (não sobrescreve); permite desfazer/refazer |
| Chat de ajustes | Camada separada sobre a mesma infraestrutura LangChain da Etapa 4 do `etapas.md`, mas com **tool calling**: o LLM não gera o vídeo, ele chama funções que editam o `project.json` (trocar, remover, mover, ajustar duração/intensidade) |
| Escopo do chat na v1 | Só comandos sobre elementos que já existem (trocar, remover, ajustar parâmetros). Pedidos que exigem replanejar do zero ("refaz as imagens todas") caem de volta no plano completo da Etapa 8, não em edição incremental |
| Preview de baixo custo | Antes de renderizar em qualidade final, gerar um preview rápido (resolução menor, sem reprocessar áudio) do segmento editado, para o usuário confirmar antes do render pesado |

### Estrutura de pastas alvo

```
src/editing/
├── __init__.py
├── project_schema.py   # project.json versionado, com ids estáveis por elemento
├── segments.py          # divide o render em segmentos independentes e cacheáveis
├── incremental_render.py # re-renderiza só os segmentos invalidados
├── replace.py            # troca manual de imagem/vídeo por outro (busca ou upload)
├── history.py             # versionamento e desfazer/refazer
└── chat/
    ├── agent.py           # LLM com tool calling sobre o project.json
    ├── tools.py            # funções que o agente pode chamar (trocar, remover, mover, ajustar)
    └── prompts/
```

## Etapa 0: `project.json` como documento único e versionado

**Objetivo:** ter uma única fonte de verdade com `id` estável por elemento, antes de qualquer coisa de edição.

**Tarefas**
- `editing/project_schema.py`: consolidar num só schema Pydantic os cortes, o rastreio de rosto (resumo por crop-keyframe), zooms, imagens, cutaways de b-roll, legendas/destaques — cada item com `id` único (ex.: `img_003`, `broll_001`, `zoom_002`).
- Migrar o formato atual (Etapa 1 do `etapas.md`) para esse schema mais completo, mantendo compatibilidade (script de migração se já houver projetos salvos).
- Salvar/carregar continua em `project.json`, agora bem mais rico.

**Critérios de aceite**
- Um projeto gerado do zero produz um `project.json` onde todo elemento visível no vídeo final tem um `id` rastreável.
- Migração de um projeto no formato antigo não perde informação.

## Etapa 1: Segmentação do render

**Objetivo:** poder re-renderizar só um pedaço, não o vídeo inteiro.

**Tarefas**
- `editing/segments.py`: dividir a timeline final em segmentos (ex.: por corte de emenda + por elemento sobreposto), cada um com hash próprio dependendo dos elementos que o afetam.
- `editing/incremental_render.py`: dado um conjunto de `id`s alterados, calcular quais segmentos precisam ser re-renderizados e remontar o vídeo final concatenando os segmentos válidos (do cache) com os recém-renderizados.

**Critérios de aceite**
- Alterar um único elemento (ex.: trocar uma imagem) re-renderiza em uma fração do tempo do render completo, e o resultado final é indistinguível de um render do zero equivalente.
- Segmentos não afetados nunca são reprocessados.

## Etapa 2: Substituição manual de imagens e vídeos

**Objetivo:** trocar o que o agente escolheu, pela UI.

**Tarefas**
- `editing/replace.py`: dado um `id` de imagem ou cutaway de b-roll, permitir (a) nova busca com outra query, (b) escolher entre os resultados alternativos da busca original, (c) upload de um arquivo próprio do usuário.
- Reaproveita `images.py` (Etapa 8) e `broll/source.py` (Parte 3) para buscar e normalizar o substituto.
- Ao confirmar a troca, aciona o render incremental (Etapa 1) só naquele elemento.

**Critérios de aceite**
- Trocar uma imagem por upload próprio funciona e aparece no vídeo final sem afetar os demais elementos.
- Trocar por um resultado alternativo da mesma busca é mais rápido que uma nova busca (cache dos resultados já retornados).

## Etapa 3: Histórico e desfazer/refazer

**Objetivo:** segurança para experimentar edições sem medo de perder o resultado bom.

**Tarefas**
- `editing/history.py`: cada edição salva uma nova versão do `project.json` (ex.: `.history/v012.json`), com um diff resumido (o que mudou).
- Comandos de desfazer/refazer na UI, restaurando a versão anterior e disparando o render incremental correspondente.
- Limite configurável de versões guardadas, com limpeza das mais antigas.

**Critérios de aceite**
- Desfazer uma troca de imagem volta exatamente ao estado (e ao vídeo) de antes da troca.
- O histórico não cresce sem limite no disco.

## Etapa 4: UI de edição pós-render

**Objetivo:** interface para editar sem depender do chat.

**Tarefas**
- Nova tela: linha do tempo com os elementos marcados (imagens, cutaways, zooms, legendas de destaque) clicáveis.
- Clicar num elemento abre o painel de troca/ajuste (Etapa 2) ou remoção.
- Preview de baixo custo (Decisão fixa acima) antes de confirmar uma troca.

**Critérios de aceite**
- Usuário consegue, sem usar o chat, localizar um elemento específico na timeline e trocá-lo em poucos cliques.

## Etapa 5: Ferramentas do agente de chat (tool calling)

**Objetivo:** dar ao LLM as funções que ele pode executar sobre o projeto.

**Tarefas**
- `chat/tools.py`: funções tipadas (Pydantic) para as ações permitidas na v1: `remover_elemento(id)`, `trocar_imagem(id, nova_query)`, `ajustar_duracao(id, nova_duracao)`, `ajustar_intensidade_zoom(id, novo_valor)`, `mover_elemento(id, novo_inicio)`, `listar_elementos(filtro?)`.
- Cada função valida contra as mesmas regras de negócio das etapas originais (densidade, sobreposição, limites de duração) antes de aplicar.
- `chat/agent.py`: usa a mesma camada LangChain (`init_chat_model`) da Etapa 4 do `etapas.md`, com `bind_tools`, em loop até o pedido do usuário ser resolvido ou o agente pedir mais informação.

**Critérios de aceite**
- O agente consegue listar os elementos existentes e executar uma troca simples chamando a função certa, com os parâmetros certos.
- Uma ação inválida (ex.: duração fora do limite) é rejeitada pela validação, não pelo bom senso do LLM.

## Etapa 6: Interface de chat

**Objetivo:** conversar com o agente sobre o vídeo já gerado.

**Tarefas**
- Painel de chat na UI, ao lado da timeline (Etapa 4), mostrando o histórico da conversa e as ações que o agente executou (não só o texto de resposta).
- Cada ação do agente aciona o render incremental (Etapa 1) e atualiza o preview automaticamente.
- Pedido ambíguo ("melhora essa parte") faz o agente perguntar de volta em vez de adivinhar.
- Pedido fora do escopo de edição incremental (ex.: "refaz todas as imagens") é identificado e encaminhado para o replanejamento completo (Etapa 8 do `etapas.md`), com aviso claro ao usuário de que isso é diferente de uma edição pontual.

**Critérios de aceite**
- Pedidos como "tira o zoom da parte 2" e "troca a imagem da cesta de frutas por outra" funcionam de ponta a ponta pelo chat.
- O usuário vê, na conversa, quais elementos foram alterados, não só uma resposta genérica de "feito".

## Etapa 7: Testes de integração e robustez

**Tarefas**
- Testes cobrindo: troca manual, troca por chat, desfazer, e uma sequência de várias edições seguidas (garantir que o histórico e o cache não se corrompem).
- Testar que uma edição não derruba elementos de outras Partes (ex.: trocar uma imagem não bagunça um cutaway de b-roll vizinho).
- Documentar no `README.md` o fluxo de edição pós-render e os comandos de chat suportados na v1.

**Critérios de aceite**
- Sequência de 5+ edições seguidas (mix de manual e chat) resulta num vídeo final coerente, sem elementos órfãos ou sobrepostos.
- `README.md` lista claramente o que o chat sabe fazer na v1 e o que ainda exige replanejamento completo.

---

## Checklist Parte 5

- [x] Etapa 0: `project.json` como documento único e versionado
- [x] Etapa 1: Segmentação do render
- [x] Etapa 2: Substituição manual de imagens e vídeos
- [x] Etapa 3: Histórico e desfazer/refazer
- [x] Etapa 4: UI de edição pós-render
- [x] Etapa 5: Ferramentas do agente de chat (tool calling)
- [x] Etapa 6: Interface de chat
- [x] Etapa 7: Testes de integração e robustez

Etapa 0 verificada: IDs estáveis no `project.json` v2, plano criativo lido do
documento, migração v1 com backup e keyframes/legendas registrados após render.
Dois projetos salvos foram migrados sem falhas; os testes e a revisão independente
aprovaram os critérios automatizáveis.

Etapa 1 verificada: o job `gerar` guarda segmentos finais por hash, invalida IDs
alterados e intervalos antigos de IDs movidos/removidos, reaproveita os demais
e concatena o vídeo com áudio contínuo. Testes sintéticos compararam quadros e
áudio com um render limpo após troca de imagem, legenda e B-roll com transição;
elementos inativos não invalidam cache. A segunda geração pela API reutilizou
todos os segmentos. A revisão independente aprovou os critérios.

Etapa 2 verificada: imagens e B-roll podem ser substituídos por nova busca,
alternativa salva ou upload. O job `substituir` conserva a timeline, usa as opções
da última geração e só confirma a mudança quando a nova mídia aparece no vídeo;
o render incremental reaproveita os outros segmentos. Os testes sintéticos de
upload e as alternativas sem nova busca passaram, assim como o build da UI e a
revisão independente. A avaliação visual na interface com mídia real está em
`testes-pendentes.md`.

Etapa 3 verificada: cada substituição concluída grava snapshots do projeto,
resumo dos IDs alterados e SHA-256 do MP4. Desfazer/refazer remonta apenas os
segmentos envolvidos e só publica o resultado se corresponder exatamente à
versão salva. O histórico descarta o ramo de refazer após nova edição, limita
o número de versões configuravelmente e rejeita trocas quando o plano tem
alterações pendentes que ainda não estão no vídeo. Testes com imagem e B-roll,
build da interface e revisão independente aprovaram os critérios; a conferência
visual com vídeo real está em `testes-pendentes.md`.

Etapa 4 verificada: a aba Edição localiza imagens, B-roll, zooms e destaques
na linha do tempo. Troca ou remoção gera uma prévia curta, reduzida e sem áudio;
o vídeo final só muda após confirmação, com versão no histórico. Testes
sintéticos cobriram os quatro tipos, troca de imagem, remoção de B-roll,
rejeição de prévia obsoleta e desfazer. A suíte completa, Ruff, doctor, build
e revisão independente aprovaram os critérios automatizáveis; a conferência
visual com vídeo real está em `testes-pendentes.md`.

Etapa 5 verificada: seis ferramentas Pydantic operam por ID sobre um rascunho;
`agent.py` chama `bind_tools` somente pelo cliente LLM compartilhado. O código
rejeita durações inválidas, sobreposição, passagem por emendas e zooms ocultos
por B-roll. Um B-roll só pode ser encurtado até uma palavra da frase original,
com o fim ajustado exatamente à transcrição. Testes com modelo falso cobriram
listagem, troca de imagem, validação de duração e retorno do resultado ao LLM;
suíte completa, Ruff, doctor e revisão independente aprovaram a etapa. A
conferência com LLM real está em `testes-pendentes.md` para a Etapa 6.

Etapa 6 verificada: a aba Edição mostra a conversa e as ferramentas com IDs ao
lado da linha do tempo. O job do chat trabalha sobre um rascunho, renderiza
automaticamente a prévia curta do trecho e só aplica a mudança ao vídeo final
após confirmação; o histórico registra a edição. Pedidos ambíguos pedem mais
detalhes e pedidos de replanejamento indicam os controles corretos. Testes
sintéticos de ponta a ponta cobriram troca de imagem, remoção de zoom,
invariância do vídeo antes da confirmação e respostas sem edição. A suíte
completa passou com 581 testes (19 integrações não executadas); os 11 testes
focados passaram após o último ajuste. Ruff, doctor, build do frontend e
revisão independente aprovaram. A conferência visual e com LLM real consta em
`testes-pendentes.md`.

Etapa 7 verificada: um teste com vídeo sintético aplicou cinco edições seguidas
(três manuais e duas pelo chat), confirmou cada prévia e comprovou que todos os
quadros do B-roll vizinho e da lacuna permaneceram iguais. Após cada mudança,
conferiu o documento por ID, os intervalos, os segmentos reutilizados, o
manifesto do cache e a versão registrada. Desfazer e refazer restauraram
exatamente os bytes das versões esperadas. O README explica o fluxo pós-render,
as ações do chat v1 e os pedidos que exigem novo planejamento. A suíte completa
passou com 582 testes (19 integrações não executadas); teste focado, Ruff,
doctor, build do frontend e revisão independente passaram. A avaliação visual
com vídeo real está em `testes-pendentes.md`.

---

# Parte 6 — Biblioteca de Efeitos de Transição

## Visão geral

Ampliar os efeitos entre câmera, B-roll e outros elementos da timeline. A Parte 3 já oferece corte seco, crossfade, deslizamento e varredura; esta parte organiza uma biblioteca maior, com prévias comparáveis e controle de intensidade, sem afetar a voz.

## Decisões fixas

| Tema | Decisão |
|---|---|
| Padrão | Corte seco continua sendo o padrão para B-roll; efeitos adicionais são opcionais |
| Áudio | Transições alteram só a imagem; a trilha de voz continua sem cortes |
| Tempo | Cada efeito preserva a duração e a grade de quadros da timeline |
| Segurança | Sem quadros pretos, congelados ou cutaways atravessando emendas de clipes |
| Desempenho | Renderizar apenas os trechos necessários; mostrar o custo estimado de cada efeito |

## Etapa 0: Catálogo e parâmetros

**Objetivo:** definir uma lista pequena de efeitos úteis antes de expô-los no editor.

**Tarefas**
- Inventariar os efeitos do `xfade` do FFmpeg e escolher presets para fade, movimento, revelação e efeitos estilizados (ex.: zoom e blur), com nomes claros e prévias.
- Definir duração, direção e intensidade permitidas por efeito; validar compatibilidade com clipes curtos e limites da timeline.

**Critérios de aceite**
- Cada preset tem nome, descrição, duração padrão e uma prévia que mostra entrada e saída.
- Parâmetros inválidos são rejeitados antes do render.

## Etapa 1: Render e testes dos novos efeitos

**Objetivo:** implementar os presets selecionados sem perder sincronia.

**Tarefas**
- Estender `src/broll/transitions.py` com os novos efeitos e uma configuração por transição.
- Aplicar a mesma infraestrutura a outras trocas de cena da timeline quando fizer sentido, preservando o áudio original.
- Medir duração, contagem de quadros, latência e uso de memória com clipes sintéticos e reais.

**Critérios de aceite**
- Todos os efeitos preservam duração e áudio; nenhum gera quadro preto ou travado nas bordas.
- Um efeito indisponível no FFmpeg instalado cai para o corte seco com aviso claro.

## Etapa 2: Escolha e prévia na interface

**Objetivo:** permitir comparar efeitos antes de renderizar o vídeo completo.

**Tarefas**
- Mostrar o catálogo com prévia curta da entrada e da saída, sem baixar novamente o B-roll.
- Permitir escolher o efeito padrão do projeto e substituir o efeito de um cutaway específico, com duração/direção dentro dos limites.
- Salvar a escolha no projeto e reaproveitá-la ao re-renderizar.

**Critérios de aceite**
- Trocar um efeito na prévia altera só a transição escolhida no render seguinte.
- O usuário consegue comparar pelo menos três presets lado a lado com a mesma cena e áudio.

---

## Checklist Parte 6

- [x] Etapa 0: Catálogo e parâmetros
- [x] Etapa 1: Render e testes dos novos efeitos
- [ ] Etapa 2: Escolha e prévia na interface

Etapa 0 verificada: o catálogo inventaria os efeitos `xfade` do FFmpeg instalado
e seleciona sete presets: corte seco (0 s), fusão (0,25 s), deslizamento e
varredura (0,25 s), revelação (0,30 s), zoom (0,35 s) e desfoque (0,30 s).
Movimento e revelação aceitam esquerda/direita/cima/baixo; a fusão oferece
intensidade suave/normal/marcada; os demais usam intensidade fixa. O código
quantiza tempos a 30 fps e rejeita duração, direção, intensidade ou intervalo
incompatível com o cutaway ou a emenda antes do render. Sete MP4s sintéticos
de 4,5 s mostram entrada e saída em `output/parte6_etapa0_catalogo/`, com
`index.html` e `catalogo.json` (inventário e custo local medido). Os 22 testes
focados passaram após o ajuste de FPS; a suíte completa teve 603 testes
aprovados e 19 integrações não executadas. Ruff, doctor, build do frontend e
revisão independente aprovaram. A escolha estética das prévias está em
`testes-pendentes.md`.

Etapa 1 implementada: os sete presets entram no render de B-roll; cada entrada
e saída pode ter configuração própria em `TransitionConfig`. O áudio é copiado
da câmera, e o filtro ausente cai para corte seco com aviso. As emendas entre
clipes permanecem sem efeitos porque seus tempos e o áudio não podem mudar.
O benchmark reproduzível em `API/src/broll/benchmark.py` mediu 120 quadros,
4,0 s e áudio idêntico nos sete presets, tanto em fonte sintética quanto em
um recorte real reduzido a 360x640. Latência do efeito: 0,23–0,38 s; pico de
memória do processo FFmpeg: 145–172 MiB nesta máquina. Medidas detalhadas:
`output/parte6_etapa1_benchmark.json`. A seleção individual persistida e a
prévia na interface são da Etapa 2.
Os testes automatizados, doctor, Ruff e a revisão independente aprovaram a etapa.
