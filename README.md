# Editor de Vídeos

Transforma clipes gravados na horizontal ou na vertical em **um vídeo 1080x1920 pronto para
Reels, TikTok e Shorts**: corta as pausas e os erros de fala, enquadra seguindo o rosto, queima
legendas palavra por palavra, coloca fotos sobre a fala e dá zooms nos momentos de ênfase.

Roda na sua máquina. São duas partes: a **API** (Python, em `API/`), que faz todo o
processamento, e a **interface web** (em `frontend/`), que conversa com ela.

Fluxo: clipes → transcrição → cortes → 9:16 seguindo o rosto → legendas → imagens e zooms → vídeo final.

## O que você precisa

| | |
|---|---|
| **FFmpeg e ffprobe** | no PATH (`ffmpeg -version` tem que responder) |
| **Python 3.12** com [uv](https://docs.astral.sh/uv/) | o `uv` cuida do ambiente e das dependências |
| **Node.js 20+** | só para a interface |
| GPU NVIDIA (opcional) | deixa a transcrição muito mais rápida; sem ela, use um modelo menor do Whisper |
| Chave da OpenAI (opcional) | para cortar erros de fala e sugerir imagens e zooms |
| Chave do Pexels ou Pixabay (opcional) | para as fotos que aparecem sobre a fala |

Sem as chaves o editor funciona: corta silêncios, enquadra em 9:16 e legenda, tudo offline.

## Instalação

```bash
# backend
cd API
uv sync                 # (ou `uv sync --extra gpu` para usar a GPU no Whisper sem CUDA instalado)
cp .env.example .env    # e preencha as chaves que você tiver

# interface
cd ../frontend
npm install
```

Confira o ambiente a qualquer momento:

```bash
cd API && uv run python -m src.doctor
```

Ele diz o que falta (FFmpeg, GPU, pacotes, chaves, modelo de rosto).

## Configuração (`.env`)

O arquivo pode ficar na raiz do repositório ou em `API/.env` (este tem precedência).

| chave | para que serve |
|---|---|
| `LLM_MODEL` | modelo padrão, no formato `provedor:modelo` (ex.: `openai:gpt-5-mini`) |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | acesso ao LLM (cortes de fala, imagens e zooms) |
| `WHISPER_MODEL` | `large-v3-turbo` na GPU; `small` ou `medium` se for rodar na CPU |
| `WHISPER_DEVICE` | `auto`, `cuda` ou `cpu` |
| `AUDIO_AGGRESSIVENESS` | força da limpeza de áudio: 0 não limpa, 0,5 (padrão) tira ~20 dB de ruído, 1 limpa ao máximo |
| `AUDIO_TARGET_LUFS` | volume alvo do áudio limpo (padrão −16 LUFS) |
| `PEXELS_API_KEY` / `PIXABAY_API_KEY` | busca de fotos e vídeos de B-roll |
| `CACHE_DIR`, `DATA_DIR`, `LOG_DIR`, `LOG_LEVEL` | onde ficam cache, projetos e logs |

As chaves nunca saem da sua máquina, a não ser nas chamadas aos próprios provedores.

## Como rodar

**Um comando só** (interface já compilada):

```bash
cd frontend && npm run build     # uma vez, ou quando a interface mudar
cd ../API && uv run python -m src.api
```

Abra http://127.0.0.1:8000. A documentação da API fica em `/docs`.

**Durante o desenvolvimento** (recarrega ao salvar), em dois terminais:

```bash
cd API && uv run python -m src.api --reload      # http://127.0.0.1:8000
cd frontend && npm run dev                       # http://localhost:5173
```

## Usando

1. **Crie um projeto** (barra de projetos, embaixo) e dê um nome.
2. **Traga os vídeos**: "Abrir pasta" importa uma pasta inteira em ordem natural (1, 2, 10);
   "Enviar" sobe arquivos na ordem em que você selecionou. Arraste os cartões para mudar a ordem
   (ou use as setas ← → no teclado).
3. **Escolha as opções** na coluna da direita: estabilizar os clipes, cortar silêncios,
   cortar erros de fala com o LLM, vertical 9:16, legendas (e o estilo delas),
   imagens sobre a fala, zooms no rosto, B-roll e o modelo do LLM.
4. **Veja a prévia** com "Sugerir imagens e zooms": o LLM escolhe as palavras, e você troca a
   foto (◀ ▶), muda a busca, ou desliga o que não gostou. Nada disso chama o LLM de novo.
5. Se ativou **B-roll**, clique em "Preparar B-roll". Na aba "Plano criativo", assista aos
   vídeos sugeridos e aprove os que combinam com as frases. Pode desativar um cutaway ou
   trocar a busca; depois de trocar, prepare novamente para ver o novo vídeo.
6. **"Gerar vídeo"**: o trabalho vira um job na fila da API, com progresso por etapa e botão
   cancelar. A interface não trava; pode até fechar a aba e voltar depois.
7. **Assista e baixe** o resultado no palco, na aba "Resultado".

Os projetos, os vídeos enviados e os resultados ficam em `API/data/projects/<id>/`. O cache
(transcrições, rastreio de rosto, respostas do LLM e fotos baixadas) fica em `API/.cache/` — pode
apagar à vontade, só custa refazer.

### Prévia de B-roll pela API

O mesmo fluxo está disponível em `/docs`: crie um job `broll` em
`POST /api/projects/{id}/jobs` com `opcoes.broll=true`. Depois consulte `GET /api/projects/{id}/broll`:
cada item mostra a frase, a busca, a duração e a URL do vídeo escolhido. Use
`PATCH /api/projects/{id}/broll/{item_id}` com `{"aprovado":true}` para incluir o
cutaway no próximo render, `{"ativo":false}` para removê-lo ou `{"query":"nova busca"}`
para trocar o vídeo. Após mudar a busca, rode o job `broll` novamente e aprove o novo
resultado. Essas ações reaproveitam o plano salvo; não chamam o LLM outra vez.
`broll_intervalo_min` controla a distância entre o início dos cutaways (8–30 s) e
`broll_transition` aceita `hard_cut`, `crossfade`, `slide` ou `wipe`. As escolhas ficam
salvas no `project.json` ao iniciar um job com opções. Imagens ativas têm prioridade se
alguém editar o plano após a prévia; o render também omite zooms cobertos por B-roll.

### Legendas de destaque

A Parte 4 escolhe trechos literais de até cinco palavras a partir da transcrição após
os cortes. O plano salva os destaques junto a imagens, zooms e B-roll. O gerador
`src.highlight_captions.ass_builder.write_highlights_project` produz um `.ass` com
revelação palavra a palavra, permanência padrão de 1,2 s e fade. A fonte padrão é
menor e o texto procura uma área livre, longe das caixas de rosto fornecidas. Veja
`output/parte4_etapa1_fala_real_curta.mp4` para uma amostra com voz. O JSON antigo
`output/parte4_etapa0_plano_destaques.json` é histórico e contém trechos anteriores
ao limite de cinco palavras; precisa ser replanejado para novo uso. O exemplo atual
com três trechos escolhidos pelo LLM está em `output/parte4_etapa1_plano_curto.json`.
Na interface, escolha **Nenhuma**, **Legenda contínua** ou **Legendas de destaque** no
seletor de legendas. O modo Destaque permite ajustar permanência, tamanho e cores.
O projeto e a API rejeitam a combinação simultânea dos dois modos. Ao gerar de novo,
o pipeline usa apenas o modo escolhido e refaz planos de destaque antigos ou com
permanência alterada.

## Pela linha de comando

```bash
cd API
uv run python -m src.pipeline ../samples -o ../output/final.mp4     # pipeline completo
uv run python -m src.pipeline v1.mp4 v2.mp4 -o saida.mp4 --sem-imagens --sem-zooms
uv run python -m src.audio.optimize aula.mp4 limpo.wav                  # limpar o áudio
uv run python -m src.audio.optimize podcast.mp3 limpo.mp3 --aggressiveness 0.8
uv run python -m src.video.stabilize tremido.mp4 estavel.mp4 --smoothing medio --crop 5
uv run python -m src.video.stabilize tremido.mp4 estavel_opencv.mp4 --metodo opencv
uv run python -m src.transcribe clipe.mp4                           # só a transcrição
uv run python -m src.face clipe.mp4 -o debug.mp4                    # rastreio de rosto
uv run python -m src.render projeto.project.json -o final.mp4       # render básico da timeline
```

`--sem-cortes`, `--sem-llm`, `--sem-reenquadrar`, `--sem-legendas`, `--sem-imagens`, `--sem-zooms`
e `--sticker` ligam e desligam as etapas.

### Documento de edição do projeto

O `project.json` v2 reúne a timeline e os elementos com IDs estáveis: clipes,
cortes, crops, imagens, zooms, B-roll e legendas/destaques. O plano criativo usado
pela API e pelo pipeline é reconstruído desse arquivo. Projetos v1 são migrados
ao abrir; o arquivo original fica em `project.v1.json` e um
`plano_imagens.json` antigo é incorporado ao documento. Para migrar todos os
projetos salvos de uma vez, rode `cd API` e
`uv run python -m src.editing.migrate`. Um sidecar legado pode continuar no disco
para compatibilidade, mas o `project.json` é a fonte usada na geração.

O comando `src.render` acima é o render básico da timeline para depuração; ele
não monta os efeitos do plano criativo. A interface/API gera o vídeo completo
a partir do documento; `src.pipeline` cria um novo documento a partir dos clipes.

### Render por segmentos

O job **Gerar vídeo** guarda segmentos prontos em
`API/data/projects/<id>/render_cache/`. Ao gerar novamente, reaproveita os
trechos cujas fontes, decisões e efeitos continuam iguais. O resultado do job
informa `segmentos_renderizados` e `segmentos_reutilizados`. Para solicitar uma
edição pontual pela API, envie os IDs alterados no corpo de
`POST /api/projects/<id>/jobs`:

```json
{"tipo": "gerar", "ids_alterados": ["img_003"]}
```

Se a geração anterior usou opções personalizadas, envie as mesmas `opcoes`
nessa chamada para conservar o plano e aproveitar o cache.

O vídeo final é remontado a partir dos segmentos válidos, com o áudio codificado
uma vez. A troca de imagens e vídeos pela interface será acrescentada na próxima
etapa; o envio de IDs já permite usar o motor incremental pela API.

## Estabilizando o vídeo

**No editor**, marque "estabilizar clipes" no grupo *Imagem* e escolha o nível
leve, médio ou forte. O processamento ocorre por clipe antes de rastrear o rosto
e de gerar o vídeo. O projeto salva a opção e o nível; a transcrição e os cortes
continuam usando os clipes originais. O resultado estabilizado de cada clipe fica
em cache e é reutilizado ao gerar novamente. A mesma opção funciona no job
"Rastrear rosto".

**Sozinho, pela CLI**, rode `uv run python -m src.video.stabilize entrada.mp4 saida.mp4`
dentro de `API/`. `--smoothing leve|medio|forte` escolhe a suavização e `--crop 5`
acrescenta 5% de zoom ao recorte automático das bordas. A CLI mostra as duas
passadas; `--sem-cache` força novo processamento.

O modo automático prefere os filtros `vidstabdetect` e `vidstabtransform` do
FFmpeg. Se o FFmpeg não os tiver, usa o fallback OpenCV, que acompanha pontos de
referência entre quadros, suaviza o movimento e recorta as bordas. Use
`--metodo opencv` para forçá-lo ou `--metodo vidstab` para exigir os filtros.
Ambos preservam o áudio original. O cache separa cada clipe, nível, crop e motor.

Os padrões vêm do `.env` em `API/` ou na raiz:

```dotenv
STABILIZE_SMOOTHING=medio
STABILIZE_CROP_PERCENT=         # vazio = recorte automático; 0 a 30 = zoom extra (%)
```

O nível do `.env` vale para novos projetos e para a CLI quando `--smoothing` é
omitido. O crop vale para a CLI e para o editor quando `--crop` é omitido.
Valores explícitos na CLI substituem esses padrões.

## Limpando o áudio

O editor tem um otimizador de áudio próprio: tira o ruído de fundo (ar-condicionado,
chiado, zumbido) com o **DeepFilterNet** e deixa o volume no padrão das redes sociais
(−16 LUFS). Dá para usar de duas formas.

**Dentro do editor**, marque "limpar o ruído do áudio do vídeo" no grupo *Áudio* das
opções (ou `limpar_audio` nas opções do job). Vale para o **áudio do vídeo gerado**; a
transcrição continua usando o áudio original, porque medimos que limpar antes de
transcrever piora o reconhecimento do Whisper. Nos ajustes finos ficam a intensidade da
limpeza e o volume alvo; os padrões vêm do `.env`.

**Sozinho, pela linha de comando** (serve para qualquer áudio ou vídeo, mesmo fora de um
projeto):

```bash
cd API
uv run python -m src.audio.optimize entrevista.mp4 limpo.wav
uv run python -m src.audio.optimize podcast.mp3 limpo.mp3 --aggressiveness 0.8 --lufs -14
```

Ele mostra o antes e o depois (nível da fala, ruído, SNR e volume) e a saída é sempre
mono a 48 kHz. Medido com fala real e ruído sintético, 20 s de áudio:

| ruído | SNR antes | SNR depois | volume antes | volume depois |
|---|---|---|---|---|
| chiado (banda toda) | 9,4 dB | 27,4 dB | −26,5 LUFS | −15,8 LUFS |
| ambiente (ar-condicionado + zumbido) | 17,9 dB | 36,8 dB | −27,1 LUFS | −15,8 LUFS |

**Eco não é ruído.** Num áudio com reflexão de parede, a limpeza ajusta o volume e tira o
chiado, mas o eco continua: medindo a reflexão em si (autocorrelação no atraso dela), ela
cai de 0,30 para 0,27 — praticamente nada. Para isso, o jeito é gravar num ambiente com
menos eco. (O SNR nesse caso engana: o DeepFilterNet zera os trechos entre as palavras e
o número dispara sem que o eco tenha saído.)

Na primeira vez o DeepFilterNet (~27 MB) é baixado para o cache. Sem ele (ou sem rede),
a limpeza cai para o `noisereduce` e, por último, para o `afftdn` do FFmpeg.

## Quando algo dá errado

A interface mostra a mensagem já explicada ("Instale o FFmpeg...", "A chave da API do provedor do
LLM parece inválida..."), com o detalhe técnico no fim e o log completo do job logo abaixo.

| sintoma | o que fazer |
|---|---|
| "FFmpeg não encontrado" | instale o FFmpeg e deixe `ffmpeg` e `ffprobe` no PATH |
| Erro de CUDA / cuDNN | `WHISPER_DEVICE=cpu` no `.env`, ou `uv sync --extra gpu` |
| Chave inválida ou sem saldo | confira o `.env`; ou desligue cortes por LLM, imagens e zooms |
| Vídeo sem rosto detectado | o job avisa; o enquadramento fica centralizado e não há zoom |
| Vídeo com fps variável | o job avisa; reexporte com fps fixo se o enquadramento atrasar |
| Fonte HDR | é convertida para SDR automaticamente |
| Falha ao limpar o áudio | o vídeo sai com o áudio original e o job avisa qual clipe falhou |

## Testes

```bash
cd API
uv run pytest -q                  # rápidos (sem GPU, sem rede, sem LLM pago)
uv run pytest -q -m integration   # lentos: vídeos reais em samples/, GPU e LLM de verdade
cd ../frontend && npm run build   # checa os tipos da interface
```

## Documentação do projeto

- `etapas.md`: o plano, as decisões fixas e o checklist das etapas.
- `CLAUDE.md`: como o código está organizado e as regras que ele respeita.
- `testes-pendentes.md`: o que ainda depende de vídeos seus ou de conferência visual.
- `frontend/README.md`: detalhes da interface.
