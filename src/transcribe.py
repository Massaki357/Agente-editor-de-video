"""Transcrição por clipe com faster-whisper e timestamps por palavra, em cache.

Uso: `python -m src.transcribe clipe.mp4 [--sem-cache]`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from src.cache import file_hash, read_json_cache, write_json_cache
from src.clips import probe_clip
from src.config import Settings, get_settings

log = logging.getLogger(__name__)

# Mudar qualquer coisa que altere a saída exige subir a versão (invalida o cache).
TRANSCRIBE_VERSION = 1
LANGUAGE = "pt"
SAMPLE_RATE = 16_000

# Hesitações no prompt fazem o Whisper transcrevê-las em vez de "limpar" a fala;
# queremos vê-las para cortar depois.
INITIAL_PROMPT = "Hum, é... então, tipo, eh... né? Ah, bom, hm, deixa eu ver... tá, é isso."


class Palavra(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    indice: int
    texto: str
    inicio: float  # t_src, segundos desde o início do clipe
    fim: float
    prob: float = 1.0


class Transcricao(BaseModel):
    arquivo_hash: str
    modelo: str
    idioma: str = LANGUAGE
    duracao: float = 0.0
    palavras: list[Palavra]

    @property
    def texto(self) -> str:
        return " ".join(p.texto for p in self.palavras)


class TranscriptionError(RuntimeError):
    pass


def transcribe_clip(
    path: str | Path, *, use_cache: bool = True, settings: Settings | None = None
) -> Transcricao:
    """Transcreve um clipe. Usa o cache por hash do arquivo + parâmetros do modelo."""
    settings = settings or get_settings()
    path = Path(path)
    key = cache_key(file_hash(path), settings)

    if use_cache:
        cached = read_json_cache("transcricao", key, cache_dir=settings.cache_dir)
        if cached is not None:
            log.debug("Transcrição em cache: %s", path.name)
            return Transcricao.model_validate(cached)

    started = time.perf_counter()
    result = _transcribe_uncached(path, settings)
    log.info(
        "Transcrito %s: %d palavras em %.1f s",
        path.name,
        len(result.palavras),
        time.perf_counter() - started,
    )
    write_json_cache("transcricao", key, result.model_dump(), cache_dir=settings.cache_dir)
    return result


def cache_key(arquivo_hash: str, settings: Settings) -> str:
    params = {
        "v": TRANSCRIBE_VERSION,
        "modelo": settings.whisper_model,
        "idioma": LANGUAGE,
        "prompt": INITIAL_PROMPT,
    }
    digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:8]
    # Chave curta: caminhos no Windows têm limite de 260 caracteres.
    return f"{arquivo_hash[:32]}-{digest}"


def _transcribe_uncached(path: Path, settings: Settings) -> Transcricao:
    arquivo_hash = file_hash(path)
    with tempfile.TemporaryDirectory(prefix="editor_audio_") as tmp:
        wav = Path(tmp) / "audio.wav"
        if not extract_audio(path, wav):
            log.warning("%s não tem áudio; transcrição vazia.", path.name)
            return Transcricao(
                arquivo_hash=arquivo_hash,
                modelo=settings.whisper_model,
                duracao=probe_clip(path).duracao,
                palavras=[],
            )

        device = resolve_device(settings.whisper_device)
        try:
            raw_words, duracao = _run_whisper(wav, settings.whisper_model, device)
        except Exception as exc:  # erros de CUDA/ctranslate2 variam (RuntimeError, ValueError...)
            if device != "cuda" or settings.whisper_device == "cuda":
                raise TranscriptionError(f"Whisper falhou em {path.name}: {exc}") from exc
            log.warning("Whisper falhou na GPU (%s); tentando na CPU.", exc)
            raw_words, duracao = _run_whisper(wav, settings.whisper_model, "cpu")

    return Transcricao(
        arquivo_hash=arquivo_hash,
        modelo=settings.whisper_model,
        duracao=duracao,
        palavras=build_words(raw_words),
    )


def extract_audio(video: Path, wav: Path) -> bool:
    """Extrai o áudio em WAV 16 kHz mono. Devolve False se o vídeo não tem áudio."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", str(video)]
    cmd += ["-vn", "-map", "0:a:0?", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le"]
    try:
        proc = subprocess.run(
            [*cmd, str(wav)], capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError as exc:
        raise TranscriptionError("ffmpeg não encontrado no PATH") from exc
    if proc.returncode != 0:
        if "does not contain any stream" in proc.stderr or "Output file is empty" in proc.stderr:
            return False
        raise TranscriptionError(f"ffmpeg falhou ao extrair áudio: {proc.stderr.strip()}")
    # `0:a:0?` faz o ffmpeg criar um WAV só com cabeçalho quando não há áudio.
    return wav.exists() and wav.stat().st_size > 1024


def build_words(raw_words: list[dict[str, Any]]) -> list[Palavra]:
    """Normaliza palavras do Whisper: tira espaços, descarta vazias, reindexa e ordena tempos."""
    palavras: list[Palavra] = []
    ultimo_fim = 0.0
    for raw in raw_words:
        texto = str(raw["texto"]).strip()
        if not texto:
            continue
        inicio = max(float(raw["inicio"]), ultimo_fim)
        fim = max(float(raw["fim"]), inicio)
        palavras.append(
            Palavra(
                indice=len(palavras),
                texto=texto,
                inicio=round(inicio, 3),
                fim=round(fim, 3),
                prob=round(float(raw.get("prob", 1.0)), 3),
            )
        )
        ultimo_fim = fim
    return palavras


def resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:  # sem ctranslate2 funcional, a CPU é o caminho seguro
        return "cpu"


def _run_whisper(wav: Path, model_name: str, device: str) -> tuple[list[dict[str, Any]], float]:
    model = load_model(model_name, device)
    segments, info = model.transcribe(
        str(wav),
        language=LANGUAGE,
        word_timestamps=True,
        vad_filter=True,
        initial_prompt=INITIAL_PROMPT,
        beam_size=5,
    )
    # `segments` é um gerador: a transcrição (e erros de CUDA) acontecem aqui.
    words = [
        {"texto": w.word, "inicio": w.start, "fim": w.end, "prob": w.probability}
        for seg in segments
        for w in (seg.words or [])
    ]
    return words, float(info.duration)


@lru_cache(maxsize=2)
def load_model(model_name: str, device: str):
    """Carrega (uma vez por processo) o modelo do Whisper."""
    _register_nvidia_dlls()
    from faster_whisper import WhisperModel

    compute_type = "float16" if device == "cuda" else "int8"
    log.info("Carregando Whisper %s (%s, %s)", model_name, device, compute_type)
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def _register_nvidia_dlls() -> None:
    """No Windows, torna visíveis os DLLs de `uv sync --extra gpu` (nvidia-cublas/cudnn)."""
    if sys.platform != "win32":
        return
    try:
        import nvidia  # pacote namespace dos wheels nvidia-*-cu12
    except ImportError:
        return
    for root in getattr(nvidia, "__path__", []):
        for bin_dir in Path(root).glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def main(argv: list[str] | None = None) -> int:
    from src.logging_setup import setup_logging

    parser = argparse.ArgumentParser(prog="python -m src.transcribe", description=__doc__)
    parser.add_argument("arquivo", type=Path)
    parser.add_argument("--sem-cache", action="store_true", help="ignora o cache e retranscreve")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    setup_logging()

    started = time.perf_counter()
    result = transcribe_clip(args.arquivo, use_cache=not args.sem_cache)
    elapsed = time.perf_counter() - started

    for p in result.palavras:
        print(f"{p.indice:>4}  {p.inicio:7.2f} → {p.fim:7.2f}  {p.texto}")
    print(f"\n{len(result.palavras)} palavras, modelo {result.modelo}, {elapsed:.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
