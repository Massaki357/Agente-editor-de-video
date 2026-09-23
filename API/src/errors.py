"""Mensagens de erro em português, com o que fazer a seguir (Etapa 11).

O núcleo lança exceções técnicas (`RenderError`, `ClipProbeError`, `TranscriptionError`,
`LLMError`, `OSError`...). A API guarda no job — e o frontend mostra — o texto de
`mensagem_amigavel`, que diz a causa provável e o próximo passo. A mensagem original
continua no log do job.
"""

from __future__ import annotations

import re

DOCTOR = "rode `uv run python -m src.doctor` em API/ para conferir o ambiente"

# (padrão no texto do erro, mensagem para o usuário). A ordem importa: a 1ª que casa vence.
_REGRAS: list[tuple[re.Pattern[str], str]] = [
    (
        # o arquivo é o problema, não o FFmpeg (esta regra vem antes da dele de propósito)
        re.compile(
            r"não tem stream de vídeo|duração desconhecida|moov atom|invalid data|"
            r"não tem áudio|formato desconhecido|codec.*not supported",
            re.I,
        ),
        "O arquivo não parece um vídeo válido (ou está incompleto/corrompido). Abra-o num "
        "player para conferir e reexporte se precisar.",
    ),
    (
        # o executável precisa estar colado ao "não encontrado": "ffprobe falhou em
        # a.mp4: No such file or directory" é arquivo sumido, não FFmpeg ausente
        re.compile(
            r"(?:ffmpeg|ffprobe)[\s'\"`]*(?:não (?:foi )?encontrado|not found|"
            r"command not found|não está no path|is not recognized)"
            r"|(?:no such file or directory|winerror 2|errno 2)[:\s'\"`\[\]]*"
            r"(?:ffmpeg|ffprobe)\b",
            re.I,
        ),
        f"FFmpeg não encontrado. Instale o FFmpeg e deixe `ffmpeg` e `ffprobe` no PATH ({DOCTOR}).",
    ),
    (
        # clipe movido, renomeado ou apagado depois de importado
        re.compile(
            r"arquivo do clipe não encontrado|no such file or directory|"
            r"não existe|cannot find the (?:file|path)",
            re.I,
        ),
        "O arquivo do vídeo não está mais onde foi importado (movido, renomeado ou apagado). "
        "Importe a pasta de novo ou remova o clipe do projeto.",
    ),
    (
        re.compile(r"pexels|pixabay", re.I),
        "A busca de fotos falhou. Confira `PEXELS_API_KEY` / `PIXABAY_API_KEY` no `.env` e a sua "
        "conexão, ou desligue as imagens nas opções.",
    ),
    (
        re.compile(r"api[_ ]?key|apikey|unauthorized|authentication|401|403|invalid.*key", re.I),
        "A chave da API do provedor do LLM parece inválida ou ausente. Confira `OPENAI_API_KEY` "
        f"(ou `ANTHROPIC_API_KEY`) no `.env` ({DOCTOR}).",
    ),
    (
        re.compile(r"rate limit|429|quota|insufficient_quota", re.I),
        "O provedor do LLM recusou por limite de uso ou saldo. Espere um pouco, troque o modelo "
        "nas opções ou desligue os cortes por LLM, as imagens e os zooms.",
    ),
    (
        re.compile(r"model.*(not found|does not exist)|unknown model|modelo inexistente", re.I),
        "O modelo de LLM escolhido não existe para a sua chave. Escolha outro em Opções → "
        "modelo do LLM.",
    ),
    (
        re.compile(r"cuda|cublas|cudnn|out of memory|gpu", re.I),
        "A GPU falhou (CUDA/cuDNN ou memória). Rode com `WHISPER_DEVICE=cpu` no `.env` para usar "
        f"a CPU, ou instale as dependências de GPU com `uv sync --extra gpu` ({DOCTOR}).",
    ),
    (
        re.compile(r"ffmpeg|ffprobe", re.I),
        f"O FFmpeg falhou ao processar o vídeo. Confira o arquivo e o ambiente ({DOCTOR}).",
    ),
    (
        re.compile(r"conexão|connection|timed out|timeout|network|resolve", re.I),
        "Falha de conexão com um serviço externo. Confira a internet e tente de novo; sem LLM e "
        "sem imagens o editor funciona offline.",
    ),
    (
        re.compile(r"espaço|no space left|disk", re.I),
        "Sem espaço em disco para os arquivos temporários do render. Libere espaço e tente "
        "de novo.",
    ),
]


def mensagem_amigavel(exc: BaseException) -> str:
    """Texto para o usuário: explicação + o que fazer, mantendo o detalhe técnico no fim."""
    detalhe = (str(exc) or exc.__class__.__name__).strip()
    for padrao, texto in _REGRAS:
        if padrao.search(detalhe):
            return f"{texto} (detalhe: {_resumir(detalhe)})"
    return detalhe


def _resumir(texto: str, limite: int = 300) -> str:
    uma_linha = " ".join(texto.split())
    return uma_linha if len(uma_linha) <= limite else uma_linha[: limite - 1] + "…"
