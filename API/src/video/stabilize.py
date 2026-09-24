"""Estabilização local com vidstab ou fallback OpenCV.

`stabilize_video(entrada, saida, smoothing=None, crop_percent=None)` detecta
o movimento da câmera, suaviza a trajetória e conserva o áudio original. Sem
`crop_percent`, o zoom adaptativo do vidstab (ou o crop calculado pelo OpenCV)
cobre as bordas automaticamente. O cache inclui o motor usado.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from src.cache import cache_path, file_hash
from src.config import Settings, get_settings
from src.doctor import VIDSTAB_FILTERS, _ffmpeg_filters

log = logging.getLogger(__name__)

Smoothing = Literal["leve", "medio", "forte"]
Metodo = Literal["auto", "vidstab", "opencv"]
SMOOTHING_FRAMES: dict[str, int] = {"leve": 5, "medio": 12, "forte": 24}
CADEIA_VERSION = 2
Progresso = Callable[[str, float], None]


def _require_vidstab() -> None:
    filtros = _ffmpeg_filters()
    if filtros is None:
        raise RuntimeError("FFmpeg não está disponível; instale-o e confira o PATH")
    faltando = set(VIDSTAB_FILTERS) - filtros
    if faltando:
        raise RuntimeError(
            f"FFmpeg sem {', '.join(sorted(faltando))}; instale uma versão com "
            "--enable-libvidstab ou escolha o fallback OpenCV"
        )


def selecionar_metodo(metodo: Metodo = "auto") -> Literal["vidstab", "opencv"]:
    """Escolhe vidstab se presente; sem ele usa OpenCV automaticamente."""
    if metodo not in ("auto", "vidstab", "opencv"):
        raise ValueError("método precisa ser auto, vidstab ou opencv")
    filtros = _ffmpeg_filters()
    if filtros is None:
        raise RuntimeError("FFmpeg não está disponível; instale-o e confira o PATH")
    if metodo == "vidstab":
        _require_vidstab()
        return "vidstab"
    if metodo == "opencv":
        return "opencv"
    return "vidstab" if set(VIDSTAB_FILTERS) <= filtros else "opencv"


def cache_key(
    entrada: Path, smoothing: Smoothing, crop_percent: float | None, motor: str = "vidstab"
) -> str:
    """Identifica o resultado pelo arquivo de entrada e pelos parâmetros efetivos."""
    dados = [CADEIA_VERSION, file_hash(entrada), smoothing, crop_percent, motor]
    return hashlib.sha256(json.dumps(dados).encode()).hexdigest()[:24]


def stabilize_video(
    caminho_entrada: str | Path,
    caminho_saida: str | Path,
    smoothing: Smoothing | None = None,
    crop_percent: float | None = None,
    *,
    settings: Settings | None = None,
    use_cache: bool = True,
    on_progress: Progresso | None = None,
    metodo: Metodo = "auto",
) -> Path:
    """Estabiliza um vídeo MP4 sem recodificar o áudio.

    `smoothing` usa janelas de 11, 25 e 49 frames (leve/médio/forte) no
    `vidstabtransform` ou no filtro temporal do OpenCV. `crop_percent`
    acrescenta zoom ao mínimo automático. A saída é escrita atomicamente.
    """
    entrada, saida = Path(caminho_entrada).resolve(), Path(caminho_saida).resolve()
    if not entrada.is_file():
        raise ValueError(f"vídeo de entrada não encontrado: {entrada}")
    if entrada == saida:
        raise ValueError("a saída precisa ser diferente do vídeo de entrada")
    if saida.suffix.lower() != ".mp4":
        raise ValueError("a saída do estabilizador precisa ser .mp4")
    settings = settings or get_settings()
    smoothing = smoothing if smoothing is not None else settings.stabilize_smoothing
    crop_percent = crop_percent if crop_percent is not None else settings.stabilize_crop_percent
    if smoothing not in SMOOTHING_FRAMES:
        raise ValueError("smoothing precisa ser leve, medio ou forte")
    if crop_percent is not None and not 0 <= crop_percent <= 30:
        raise ValueError("crop_percent precisa estar entre 0 e 30")

    motor = selecionar_metodo(metodo)
    log.info("Método de estabilização: %s%s", motor, " (fallback)" if motor == "opencv" else "")
    chave = cache_key(entrada, smoothing, crop_percent, motor)
    caminho_cache = cache_path("video_estabilizado", chave, ".mp4", settings.cache_dir)
    if use_cache and caminho_cache.is_file() and caminho_cache.stat().st_size > 0:
        log.info("Vídeo estabilizado veio do cache (%s).", chave)
        resultado = _copiar_atomico(caminho_cache, saida)
        if on_progress is not None:
            on_progress("cache", 1.0)
        return resultado

    destino = caminho_cache if use_cache else saida
    if motor == "vidstab":
        _render_vidstab(entrada, destino, SMOOTHING_FRAMES[smoothing], crop_percent, on_progress)
    else:
        from src.video.opencv_fallback import render_opencv

        render_opencv(entrada, destino, SMOOTHING_FRAMES[smoothing], crop_percent, on_progress)
    log.info(
        "Vídeo estabilizado: %s (motor %s, %s, crop %s%%)",
        entrada.name,
        motor,
        smoothing,
        crop_percent,
    )
    return _copiar_atomico(destino, saida) if use_cache else saida


def cached_video(
    entrada: str | Path,
    smoothing: Smoothing | None = None,
    *,
    settings: Settings | None = None,
    on_progress: Progresso | None = None,
) -> Path:
    """Devolve o MP4 estabilizado no cache, sem copiá-lo para cada render."""
    arquivo = Path(entrada).resolve()
    settings = settings or get_settings()
    smoothing = smoothing if smoothing is not None else settings.stabilize_smoothing
    crop_percent = settings.stabilize_crop_percent
    motor = selecionar_metodo()
    chave = cache_key(arquivo, smoothing, crop_percent, motor)
    destino = cache_path("video_estabilizado", chave, ".mp4", settings.cache_dir)
    return stabilize_video(
        arquivo,
        destino,
        smoothing,
        crop_percent,
        settings=settings,
        on_progress=on_progress,
        metodo=motor,
    )


def cached_video_file(
    entrada: str | Path, smoothing: Smoothing | None = None, settings: Settings | None = None
) -> Path | None:
    """Localiza o vídeo estabilizado, sem iniciar processamento (útil na API de leitura)."""
    arquivo = Path(entrada).resolve()
    settings = settings or get_settings()
    smoothing = smoothing if smoothing is not None else settings.stabilize_smoothing
    motor = selecionar_metodo()
    destino = cache_path(
        "video_estabilizado",
        cache_key(arquivo, smoothing, settings.stabilize_crop_percent, motor),
        ".mp4",
        settings.cache_dir,
    )
    return destino if destino.is_file() and destino.stat().st_size > 0 else None


def _render_vidstab(
    entrada: Path,
    saida: Path,
    smoothing_frames: int,
    crop_percent: float | None,
    on_progress: Progresso | None = None,
) -> None:
    """Passada 1 gera transforms.trf; passada 2 renderiza vídeo e copia áudio."""
    saida.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vidstab_", dir=saida.parent) as tmp:
        temporario = Path(tmp) / "estabilizado.mp4"
        # O cwd temporário evita escapar ':' e '\\' dos caminhos Windows no filtro.
        filtro = f"vidstabtransform=input=transforms.trf:smoothing={smoothing_frames}:optzoom=2"
        if crop_percent is not None:
            filtro += f":zoom={crop_percent:g}"
        comum = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            *(["-progress", "pipe:1", "-nostats"] if on_progress else []),
            "-y",
            "-i",
            str(entrada),
        ]
        comandos = [
            [
                *comum,
                "-map",
                "0:v:0",
                "-vf",
                "vidstabdetect=result=transforms.trf",
                "-an",
                "-f",
                "null",
                "-",
            ],
            [
                *comum,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-vf",
                filtro,
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                str(temporario),
            ],
        ]
        duracao = _duracao(entrada) if on_progress else None
        for numero, comando in enumerate(comandos, start=1):
            log.info("Estabilização: passada %s/2 (%s)", numero, entrada.name)
            etapa = "análise" if numero == 1 else "estabilização"
            _executar_ffmpeg(comando, Path(tmp), numero, etapa, duracao, on_progress)
        if not temporario.is_file() or temporario.stat().st_size == 0:
            raise RuntimeError("FFmpeg não gerou o vídeo estabilizado")
        temporario.replace(saida)


def _duracao(entrada: Path) -> float:
    """Duração para a barra; informa claramente quando o arquivo não é um vídeo válido."""
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "format=duration:stream=index",
                "-of",
                "json",
                str(entrada),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe não encontrado; instale o FFmpeg e confira o PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"vídeo de entrada não pôde ser lido pelo ffprobe: {entrada}") from exc
    try:
        info = json.loads(proc.stdout)
        if not info.get("streams"):
            raise ValueError("sem faixa de vídeo")
        duracao = float(info["format"]["duration"])
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"vídeo de entrada inválido ou sem duração: {entrada}") from exc
    if proc.returncode != 0 or not 0 < duracao < float("inf"):
        raise ValueError(f"vídeo de entrada inválido ou sem duração: {entrada}")
    return duracao


def _tempo_ffmpeg(valor: str) -> float | None:
    """Converte `out_time=HH:MM:SS.mmmmmm` do protocolo de progresso."""
    try:
        horas, minutos, segundos = valor.split(":")
        return int(horas) * 3600 + int(minutos) * 60 + float(segundos)
    except (ValueError, AttributeError):
        return None


def _executar_ffmpeg(
    comando: list[str],
    cwd: Path,
    numero: int,
    etapa: str,
    duracao: float | None,
    on_progress: Progresso | None,
) -> None:
    """Lê `-progress pipe:1` sem bloquear e encerra FFmpeg se o callback cancelar."""
    if on_progress is None:
        proc = subprocess.run(comando, cwd=cwd, capture_output=True, text=True)
        erro = proc.stderr or proc.stdout
    else:
        on_progress(etapa, 0.0)
        proc = subprocess.Popen(
            comando,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        erros: list[str] = []
        try:
            assert proc.stdout is not None
            for linha in proc.stdout:
                linha = linha.strip()
                if linha.startswith("out_time=") and duracao:
                    tempo = _tempo_ffmpeg(linha.partition("=")[2])
                    if tempo is not None:
                        on_progress(etapa, min(max(tempo / duracao, 0.0), 0.99))
                elif "=" not in linha and linha:
                    erros.append(linha)
                    erros = erros[-5:]
            proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
        erro = "\n".join(erros)
    if proc.returncode != 0:
        detalhe = (erro or "sem detalhe do FFmpeg").strip().splitlines()
        raise RuntimeError(f"FFmpeg falhou na passada {numero}: {' | '.join(detalhe[-3:])}")
    if on_progress is not None:
        on_progress(etapa, 1.0)


def _copiar_atomico(origem: Path, destino: Path) -> Path:
    if origem == destino:
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vidstab_copy_", dir=destino.parent) as tmp:
        temporario = Path(tmp) / destino.name
        shutil.copyfile(origem, temporario)
        temporario.replace(destino)
    return destino


def run_poc(entrada: Path, saida: Path) -> Path:
    """Compatibilidade com a prova de conceito da Etapa 0, sem usar cache."""
    return stabilize_video(entrada, saida, use_cache=False, metodo="vidstab")


if __name__ == "__main__":
    from src.video.cli import main

    sys.exit(main())
