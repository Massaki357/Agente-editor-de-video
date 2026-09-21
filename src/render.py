"""Render da timeline: um intermediário por trecho + concatenação (Etapa 3).

Desenho (pensado para sincronia A/V e memória constante):

1. **Passada 1, por trecho** (`_render_segment`): cada trecho `[a, b]` (tempo do
   clipe original) vira um arquivo intermediário com exatamente
   `N = round((b - a) * fps)` frames de vídeo e `N / fps` s de áudio PCM. Como
   vídeo e áudio de cada trecho têm a mesma duração exata, a soma também tem, e a
   sincronia não deriva ao longo das emendas. O áudio recebe fade de entrada e de
   saída (~25 ms) para não estalar na emenda. Na Etapa 6 esta função é trocada
   pela passada OpenCV (crop dinâmico) sem mexer no resto.
2. **Concatenação** (`_concat`): concat demuxer, vídeo copiado e áudio codificado
   em AAC uma única vez (codificar AAC por trecho criaria gaps de priming).
3. **Passada 2** (`_second_pass`): ponto de extensão para legendas `.ass` e
   overlays (Etapas 7 e 8), aplicados sobre o vídeo concatenado, já em tempo do
   vídeo final. Hoje é um no-op.

Tempos: `trechos` estão no tempo original do clipe (t_src); o arquivo de saída
está no tempo do vídeo final (t_out), em que o trecho k começa na soma das
durações (em frames inteiros) dos trechos anteriores.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from src.project import ClipMeta, Project, Timeline

log = logging.getLogger(__name__)

FFMPEG_BASE = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
STDERR_TAIL_LINES = 20
# Intermediários em MOV (H.264 + PCM): o timescale por stream mantém a grade exata
# de 1/fps. Em MKV (timestamps de 1 ms) os frames saem com jitter de +-1 ms após o
# `-c:v copy` da concatenação.
SEGMENT_EXT = ".mov"

Progress = Callable[[int, int], None]


class RenderError(RuntimeError):
    """FFmpeg falhou (a mensagem inclui o fim do stderr) ou a timeline é inválida."""


@dataclass(frozen=True)
class Segment:
    """Um trecho a renderizar.

    `inicio`/`fim` em t_src (segundos do clipe original, já na grade de 1/fps);
    `t_out` é onde o trecho começa no vídeo final; `frames` é a duração exata.
    """

    indice: int
    clip_index: int
    arquivo: Path
    inicio: float
    fim: float
    frames: int
    t_out: float
    tem_audio: bool


@dataclass(frozen=True)
class RenderSettings:
    width: int
    height: int
    fps: int
    sample_rate: int
    fade: float


# ---------------------------------------------------------------- API pública


def render_timeline(
    timeline: Timeline,
    output: Path,
    *,
    size: tuple[int, int] | None = None,
    fps: int = 30,
    sample_rate: int = 48_000,
    fade: float = 0.025,
    workers: int | None = None,
    work_dir: Path | None = None,
    progress: Progress | None = None,
) -> Path:
    """Renderiza a timeline em `output` (.mp4, H.264 + AAC).

    `size=None` usa o tamanho de exibição do primeiro clipe (arredondado para par).
    `work_dir` guarda os intermediários (útil para depuração); sem ele, usa uma
    pasta temporária apagada no fim. `progress(prontos, total)` é chamado a cada
    trecho pronto (de qualquer thread) e uma última vez após a concatenação.
    """
    output = Path(output)
    if fps <= 0 or sample_rate <= 0:
        raise RenderError(f"fps/sample_rate inválidos: {fps}, {sample_rate}")

    metas = [_clip_meta(clip.arquivo, clip.meta) for clip in timeline.clipes]
    segments = plan_segments(timeline, metas, fps)
    if not segments:
        raise RenderError("timeline sem trechos para renderizar")

    width, height = _output_size(size, metas[segments[0].clip_index])
    settings = RenderSettings(width, height, fps, sample_rate, fade)
    total = len(segments) + 1  # +1 = concatenação/passada 2
    workers = workers or min(4, os.cpu_count() or 1)

    started = time.perf_counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    with _work_directory(work_dir) as tmp:
        seg_dir = tmp / "segmentos"
        seg_dir.mkdir(parents=True, exist_ok=True)
        seg_files = _render_segments(segments, seg_dir, settings, workers, progress, total)

        concatenated = _concat(seg_files, segments, tmp / "concat.mp4", settings)
        final = _second_pass(concatenated, output, timeline, settings)
        if final != output:
            shutil.move(str(final), str(output))
        if progress:
            progress(total, total)

    log.info(
        "render: %d trechos, %.2f s de vídeo, %dx%d@%d em %.1f s → %s",
        len(segments),
        sum(s.frames for s in segments) / fps,
        width,
        height,
        fps,
        time.perf_counter() - started,
        output,
    )
    return output


def plan_segments(timeline: Timeline, metas: list[ClipMeta], fps: int) -> list[Segment]:
    """Trechos na grade de 1/fps (idempotente), sem os de 0 frames, com t_out."""
    segments: list[Segment] = []
    frames_out = 0
    for ci, (clip, meta) in enumerate(zip(timeline.clipes, metas, strict=True)):
        for inicio, fim in clip.trechos:
            a_frame = round(inicio * fps)
            b_frame = round(fim * fps)
            frames = b_frame - a_frame
            if frames <= 0:
                log.debug("trecho [%s, %s] de %s tem 0 frames; ignorado", inicio, fim, clip.nome)
                continue
            segments.append(
                Segment(
                    indice=len(segments),
                    clip_index=ci,
                    arquivo=Path(clip.arquivo),
                    inicio=a_frame / fps,
                    fim=b_frame / fps,
                    frames=frames,
                    t_out=frames_out / fps,
                    tem_audio=meta.tem_audio,
                )
            )
            frames_out += frames
    return segments


# ------------------------------------------------------------ passada 1


def _render_segments(
    segments: list[Segment],
    seg_dir: Path,
    settings: RenderSettings,
    workers: int,
    progress: Progress | None,
    total: int,
) -> list[Path]:
    files = [seg_dir / f"seg_{s.indice:04d}{SEGMENT_EXT}" for s in segments]
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_render_segment, seg, dest, settings): seg
            for seg, dest in zip(segments, files, strict=True)
        }
        try:
            for fut in as_completed(futures):
                fut.result()
                done += 1
                if progress:
                    progress(done, total)
        except BaseException:
            for f in futures:
                f.cancel()
            raise
    return files


def _render_segment(seg: Segment, dest: Path, settings: RenderSettings) -> Path:
    """Gera o intermediário de um trecho: N frames de vídeo + N/fps s de PCM estéreo.

    Seek de entrada (`-ss` antes de `-i`) é rápido e, como há re-encode, preciso.
    Ponto de troca da Etapa 6: gerar o vídeo via OpenCV (crop 9:16) mantendo o
    mesmo contrato (mesmo número de frames, mesmo áudio).
    """
    w, h, fps, sr = settings.width, settings.height, settings.fps, settings.sample_rate
    duration = seg.frames / fps
    fade = max(0.0, min(settings.fade, duration / 2))

    vf = (
        f"fps={fps},"
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    af = [f"aresample={sr}", "aformat=sample_fmts=s16:channel_layouts=stereo"]
    if fade > 0:
        af += [
            f"afade=t=in:st=0:d={fade:.6f}",
            f"afade=t=out:st={duration - fade:.6f}:d={fade:.6f}",
        ]
    af.append("apad")

    cmd = [*FFMPEG_BASE, "-ss", f"{seg.inicio:.6f}", "-i", str(seg.arquivo)]
    if seg.tem_audio:
        audio_map = "0:a:0"
    else:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r={sr}:cl=stereo"]
        audio_map = "1:a:0"
    cmd += [
        "-map", "0:v:0", "-map", audio_map,
        "-vf", vf, "-af", ",".join(af),
        "-frames:v", str(seg.frames), "-t", f"{duration:.6f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-c:a", "pcm_s16le", "-ar", str(sr), "-ac", "2",
        "-sn", "-dn",
        str(dest),
    ]  # fmt: skip
    _run_ffmpeg(cmd, f"trecho {seg.indice} ({seg.arquivo.name} [{seg.inicio:.3f}, {seg.fim:.3f}])")
    return dest


# ---------------------------------------------------------- concatenação


def _concat(
    files: list[Path], segments: list[Segment], dest: Path, settings: RenderSettings
) -> Path:
    """Concat demuxer: vídeo copiado, áudio PCM → AAC uma única vez.

    A `duration` de cada entrada é explícita (N/fps) para que o offset do trecho
    seguinte não dependa da duração que o demuxer inferir do contêiner.
    """
    lista = dest.with_name("lista.txt")
    lines = ["ffconcat version 1.0"]
    for path, seg in zip(files, segments, strict=True):
        lines.append(f"file {_concat_quote(path)}")
        lines.append(f"duration {seg.frames / settings.fps:.6f}")
    lista.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cmd = [
        *FFMPEG_BASE,
        "-f", "concat", "-safe", "0", "-i", str(lista),
        "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(settings.sample_rate), "-ac", "2",
        "-video_track_timescale", str(settings.fps * 512),
        "-movflags", "+faststart",
        str(dest),
    ]  # fmt: skip
    _run_ffmpeg(cmd, "concatenação")
    return dest


def _concat_quote(path: Path) -> str:
    """Caminho entre aspas simples para o arquivo do concat (`'` vira `'\\''`)."""
    text = path.resolve().as_posix()
    return "'" + text.replace("'", "'\\''") + "'"


# ------------------------------------------------------------- passada 2


def _second_pass(
    concatenated: Path, output: Path, timeline: Timeline, settings: RenderSettings
) -> Path:
    """Ponto de extensão das Etapas 7-8 (legendas `.ass`, overlays de imagem).

    Recebe o vídeo concatenado (tempo do vídeo final) e devolve o arquivo pronto.
    Hoje não há efeitos: só devolve o concatenado para ser movido para `output`.
    Efeitos futuros devem ser limitados por trecho (ver `plan_segments`/`t_out`)
    para não atravessar emendas.
    """
    return concatenated


# ------------------------------------------------------------- utilidades


def _clip_meta(arquivo: str, meta: ClipMeta | None) -> ClipMeta:
    if meta is not None:
        return meta
    from src.clips import ClipProbeError, probe_clip

    try:
        return probe_clip(arquivo)
    except (ClipProbeError, OSError) as exc:
        raise RenderError(f"não foi possível ler {arquivo}: {exc}") from exc


def _output_size(size: tuple[int, int] | None, first: ClipMeta) -> tuple[int, int]:
    width, height = size if size is not None else (first.largura, first.altura)
    width, height = int(width) - int(width) % 2, int(height) - int(height) % 2
    if width <= 0 or height <= 0:
        raise RenderError(f"tamanho de saída inválido: {size or (first.largura, first.altura)}")
    return width, height


@contextmanager
def _work_directory(work_dir: Path | None) -> Iterator[Path]:
    """`work_dir` do usuário (mantido) ou uma pasta temporária (apagada no fim)."""
    if work_dir is not None:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        yield work_dir
        return
    with tempfile.TemporaryDirectory(prefix="render_") as tmp:
        yield Path(tmp)


def _run_ffmpeg(cmd: list[str], what: str) -> None:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError as exc:
        raise RenderError("ffmpeg não encontrado no PATH (rode `python -m src.doctor`)") from exc
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-STDERR_TAIL_LINES:])
        raise RenderError(f"FFmpeg falhou em {what} (código {proc.returncode}):\n{tail}")


# -------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    from src.logging_setup import setup_logging

    parser = argparse.ArgumentParser(prog="python -m src.render", description=__doc__)
    parser.add_argument("projeto", type=Path, help="project.json")
    parser.add_argument("-o", "--output", type=Path, required=True, help="saída .mp4")
    parser.add_argument("--size", help="LxA, ex.: 1080x1920 (padrão: tamanho do 1º clipe)")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--work-dir", type=Path, help="mantém os intermediários nesta pasta")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    setup_logging()

    size = None
    if args.size:
        try:
            w, h = (int(v) for v in args.size.lower().split("x"))
        except ValueError:
            parser.error(f"--size inválido: {args.size}")
        size = (w, h)

    project = Project.carregar(args.projeto)

    def _progress(done: int, total: int) -> None:
        print(f"\r{done}/{total}", end="", file=sys.stderr, flush=True)

    try:
        out = render_timeline(
            project.timeline,
            args.output,
            size=size,
            fps=args.fps,
            workers=args.workers,
            work_dir=args.work_dir,
            progress=_progress,
        )
    except RenderError as exc:
        print(file=sys.stderr)
        log.error("%s", exc)
        return 1
    print(file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
