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
| `PEXELS_API_KEY` / `PIXABAY_API_KEY` | busca das fotos |
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
3. **Escolha as opções** na coluna da direita: cortar silêncios, cortar erros de fala com o LLM,
   vertical 9:16, legendas (e o estilo delas), imagens sobre a fala, zooms no rosto e o modelo do
   LLM.
4. **Veja a prévia** com "Sugerir imagens e zooms": o LLM escolhe as palavras, e você troca a
   foto (◀ ▶), muda a busca, ou desliga o que não gostou. Nada disso chama o LLM de novo.
5. **"Gerar vídeo"**: o trabalho vira um job na fila da API, com progresso por etapa e botão
   cancelar. A interface não trava; pode até fechar a aba e voltar depois.
6. **Assista e baixe** o resultado no palco, na aba "Resultado".

Os projetos, os vídeos enviados e os resultados ficam em `API/data/projects/<id>/`. O cache
(transcrições, rastreio de rosto, respostas do LLM e fotos baixadas) fica em `API/.cache/` — pode
apagar à vontade, só custa refazer.

## Pela linha de comando

```bash
cd API
uv run python -m src.pipeline ../samples -o ../output/final.mp4     # pipeline completo
uv run python -m src.pipeline v1.mp4 v2.mp4 -o saida.mp4 --sem-imagens --sem-zooms
uv run python -m src.audio.optimize aula.mp4 limpo.wav                  # limpar o áudio
uv run python -m src.audio.optimize podcast.mp3 limpo.mp3 --aggressiveness 0.8
uv run python -m src.transcribe clipe.mp4                           # só a transcrição
uv run python -m src.face clipe.mp4 -o debug.mp4                    # rastreio de rosto
uv run python -m src.render projeto.project.json -o final.mp4       # só o render
```

`--sem-cortes`, `--sem-llm`, `--sem-reenquadrar`, `--sem-legendas`, `--sem-imagens`, `--sem-zooms`
e `--sticker` ligam e desligam as etapas.

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
