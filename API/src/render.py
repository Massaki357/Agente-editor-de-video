"""Render da timeline: um intermediário por trecho + concatenação (Etapas 3 e 6).

Desenho (pensado para sincronia A/V e memória constante):

1. **Passada 1, por trecho** (`_render_segment`): cada trecho `[a, b]` (tempo do
   clipe original) vira um arquivo intermediário com exatamente
   `N = round((b - a) * fps)` frames de vídeo e `N / fps` s de áudio PCM. Como
   vídeo e áudio de cada trecho têm a mesma duração exata, a soma também tem, e a
   sincronia não deriva ao longo das emendas. O áudio recebe fade de entrada e de
   saída (~25 ms) para não estalar na emenda.
   - Sem `cameras`: o FFmpeg redimensiona o quadro inteiro (letterbox).
   - Com `cameras` (Etapa 6, `_render_segment_reframe`): o FFmpeg decodifica o
     trecho já na grade de fps, o **OpenCV** recorta a janela 9:16 que segue o rosto
     (`src.reframe.CameraPath`), redimensiona para 1080x1920 e envia os frames por
     pipe a um segundo FFmpeg, que os junta ao áudio original do trecho.
   - Com `zoom` (Etapa 9): a janela encolhe pela escala do instante (t_out) e o
     recorte é feito em coordenadas fracionárias (`cv2.warpAffine`), sem o tremor
     de 1 px que o arredondamento causaria enquanto a escala muda.
   - Fontes muito maiores que a saída (4K) são reduzidas pelo FFmpeg antes do pipe,
     até a janela mais fechada ainda cobrir a largura de saída (Etapa 11).
   - Fontes HDR (HLG/PQ) passam por tonemapping para BT.709 na decodificação.
2. **Concatenação** (`_concat`): concat demuxer, vídeo copiado e áudio codificado
   em AAC uma única vez (codificar AAC por trecho criaria gaps de priming).
3. **Passada 2** (`_second_pass`): ponto de extensão para legendas `.ass` e
   overlays (Etapas 7 e 8), aplicados sobre o vídeo concatenado, já em tempo do
   vídeo final. Hoje é um no-op.

Relação com o etapas.md: a "passada 1 (OpenCV)" é o recorte por trecho; o "juntar
com o áudio original + fades" acontece no encoder de cada trecho (assim áudio e
vídeo de cada trecho têm exatamente N/fps s e a sincronia não deriva); a
"passada 2 (FFmpeg)" é a concatenação + `_second_pass`.

Cor: com reenquadramento, a origem é decodificada com a matriz dela
(`_source_matrix`) e a saída é gravada em BT.709 com as marcações.

Tempos: `trechos` estão no tempo original do clipe (t_src); o arquivo de saída
está no tempo do vídeo final (t_out), em que o trecho k começa na soma das
durações (em frames inteiros) dos trechos anteriores.
"""

from __future__ import annotations

import argparse
import functools
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

from src.project import ClipMeta, Project, Timeline
from src.reframe import CameraPath

if TYPE_CHECKING:
    from src.broll.transitions import Cutaway, Transition
    from src.images import Overlay

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
    audio: Path | None = None  # trilha alternativa (áudio limpo da Parte 1)


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
    cameras: Mapping[int, CameraPath] | None = None,
    legendas: Path | None = None,
    overlays: Sequence[Overlay] = (),
    zoom: Callable[[float], float] | None = None,
    audios: Mapping[int, Path] | None = None,
    broll: Sequence[Cutaway] = (),
    broll_transition: Transition = "hard_cut",
) -> Path:
    """Renderiza a timeline em `output` (.mp4, H.264 + AAC).

    `legendas`: arquivo `.ass` (tempo do vídeo final) queimado na passada 2.
    `overlays`: imagens posicionadas (`src.images.Overlay`), também na passada 2.

    `cameras` (índice do clipe → caminho da janela 9:16) liga o reenquadramento:
    cada trecho é recortado pelo OpenCV e redimensionado para `size`.
    `zoom(t_out) → escala` (Etapa 9, só com `cameras`): zoom no rosto.
    `audios` (índice do clipe → WAV): usa outra trilha de áudio no lugar da do clipe
    (o áudio limpo da Parte 1 do novas-etapas.md). A limpeza mantém os tempos (medido:
    deslocamento < 1 ms), mas pode encurtar o fim em algumas dezenas de ms — o `apad`
    de cada trecho completa com silêncio.

    `broll`: cutaways já preparados em t_out, aplicados depois do reenquadramento
    e do zoom. A transição afeta só o vídeo; a voz segue do áudio concatenado.

    `size=None` usa o tamanho de exibição do primeiro clipe (arredondado para par).
    `work_dir` guarda os intermediários (útil para depuração); sem ele, usa uma
    pasta temporária apagada no fim. `progress(prontos, total)` é chamado a cada
    trecho pronto (de qualquer thread) e uma última vez após a concatenação.
    """
    output = Path(output)
    if fps <= 0 or sample_rate <= 0:
        raise RenderError(f"fps/sample_rate inválidos: {fps}, {sample_rate}")

    metas = [_clip_meta(clip.arquivo, clip.meta) for clip in timeline.clipes]
    segments = plan_segments(timeline, metas, fps, audios)
    if not segments:
        raise RenderError("timeline sem trechos para renderizar")
    if broll:
        from src.broll.transitions import validate_cutaways

        validate_cutaways(broll, segments, fps, broll_transition, 0.25)

    width, height = _output_size(size, metas[segments[0].clip_index])
    settings = RenderSettings(width, height, fps, sample_rate, fade)
    total = len(segments) + 1  # +1 = concatenação/passada 2
    workers = workers or min(4, os.cpu_count() or 1)

    started = time.perf_counter()
    output.parent.mkdir(parents=True, exist_ok=True)
    with _work_directory(work_dir) as tmp:
        seg_dir = tmp / "segmentos"
        seg_dir.mkdir(parents=True, exist_ok=True)
        seg_files = _render_segments(
            segments, seg_dir, settings, workers, progress, total, cameras or {}, zoom
        )

        concatenated = _concat(seg_files, segments, tmp / "concat.mp4", settings)
        if broll:
            from src.broll.transitions import apply_cutaways

            concatenated = apply_cutaways(
                concatenated,
                tmp / "broll.mp4",
                broll,
                segments,
                fps,
                transition=broll_transition,
            )
        final = _second_pass(concatenated, tmp, timeline, settings, legendas, overlays)
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


def plan_segments(
    timeline: Timeline,
    metas: list[ClipMeta],
    fps: int,
    audios: Mapping[int, Path] | None = None,
) -> list[Segment]:
    """Trechos na grade de 1/fps (idempotente), sem os de 0 frames, com t_out.

    `audios` (índice do clipe → WAV) troca a trilha de áudio daquele clipe: a limpeza
    não desloca o áudio, então os mesmos tempos valem.
    """
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
                    audio=(audios or {}).get(ci),
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
    cameras: Mapping[int, CameraPath],
    zoom: Callable[[float], float] | None = None,
) -> list[Path]:
    files = [seg_dir / f"seg_{s.indice:04d}{SEGMENT_EXT}" for s in segments]
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {}
        for seg, dest in zip(segments, files, strict=True):
            camera = cameras.get(seg.clip_index)
            if camera is not None:
                fut = pool.submit(_render_segment_reframe, seg, dest, settings, camera, zoom)
            else:
                fut = pool.submit(_render_segment, seg, dest, settings)
            futures[fut] = seg
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

    Vídeo: seek de entrada (`-ss` antes de `-i`) é rápido e, como há re-encode,
    preciso ao frame. Áudio: segunda entrada sem seek, cortada com `atrim` (ver
    `_audio_args`). Quadro inteiro redimensionado com letterbox (sem reenquadrar).
    """
    w, h, fps, sr = settings.width, settings.height, settings.fps, settings.sample_rate
    cor = _decode_color_filter(seg.arquivo, _altura_origem(seg.arquivo, h))
    vf = (
        f"fps={fps},{cor},"
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )
    entradas, audio_map, af = _audio_args(seg, settings, 1)
    cmd = [*FFMPEG_BASE, "-ss", f"{seg.inicio:.6f}", "-i", str(seg.arquivo), *entradas]
    cmd += [
        "-map", "0:v:0", "-map", audio_map,
        "-vf", vf, "-af", af,
        "-frames:v", str(seg.frames), "-t", f"{seg.frames / fps:.6f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-c:a", "pcm_s16le", "-ar", str(sr), "-ac", "2",
        "-sn", "-dn",
        str(dest),
    ]  # fmt: skip
    _run_ffmpeg(cmd, f"trecho {seg.indice} ({seg.arquivo.name} [{seg.inicio:.3f}, {seg.fim:.3f}])")
    return dest


@functools.lru_cache(maxsize=256)
def _is_hdr(path: Path) -> bool:
    """Fonte HLG/PQ? (o render converte para SDR; sem isto a imagem sai lavada)."""
    from src.clips import HDR_TRANSFERS

    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0"]
            + ["-show_entries", "stream=color_transfer", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return out in HDR_TRANSFERS


@functools.lru_cache(maxsize=256)
def _altura_origem(path: Path, padrao: int) -> int:
    """Altura do quadro de origem (o `_source_matrix` usa isso para adivinhar SD/HD)."""
    from src.clips import ClipProbeError, probe_clip

    try:
        return probe_clip(path).altura
    except (ClipProbeError, OSError):
        return padrao


def _decode_color_filter(path: Path, altura: int) -> str:
    """Filtro que leva a origem para BT.709: tonemapping se for HDR, matriz se não for."""
    if _is_hdr(path):
        # HDR → SDR: linear, tonemap hable e volta para BT.709 limitado
        return (
            "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
        )
    return f"scale=in_color_matrix={_source_matrix(path, altura)}"


@functools.lru_cache(maxsize=256)
def _source_matrix(path: Path, altura: int) -> str:
    """Matriz YUV→RGB da origem: a marcada no arquivo ou, sem marcação, a que os
    players assumem (BT.709 para HD, BT.601 para SD)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0"]
            + ["-show_entries", "stream=color_space", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    conhecidas = {"bt709": "bt709", "bt470bg": "bt601", "smpte170m": "bt601", "fcc": "fcc"}
    conhecidas |= {"bt2020nc": "bt2020", "bt2020c": "bt2020", "smpte240m": "smpte240m"}
    if out in conhecidas:
        return conhecidas[out]
    return "bt709" if altura >= 720 else "bt601"


def _audio_args(seg: Segment, settings: RenderSettings, input_index: int) -> tuple[list, str, str]:
    """Entradas extras, mapa e filtro do áudio do trecho (fades nas emendas).

    O áudio NÃO usa seek de entrada (`-ss` antes de `-i`): nele o AAC sai 13–40 ms
    adiantado (as amostras de "priming" não são descartadas). O corte é feito com
    `atrim` depois de decodificar, que é exato (medido: erro < 0,1 ms).
    """
    fps, sr = settings.fps, settings.sample_rate
    duration = seg.frames / fps
    fade = max(0.0, min(settings.fade, duration / 2))
    af = [f"aresample={sr}", "aformat=sample_fmts=s16:channel_layouts=stereo"]
    if seg.tem_audio:
        af = [f"atrim=start={seg.inicio:.6f}", "asetpts=PTS-STARTPTS", *af]
    if fade > 0:
        af += [
            f"afade=t=in:st=0:d={fade:.6f}",
            f"afade=t=out:st={duration - fade:.6f}:d={fade:.6f}",
        ]
    af.append("apad")
    if seg.tem_audio:
        entradas = ["-i", str(seg.audio or seg.arquivo)]
    else:
        entradas = ["-f", "lavfi", "-i", f"anullsrc=r={sr}:cl=stereo"]
    return entradas, f"{input_index}:a:0", ",".join(af)


def _render_segment_reframe(
    seg: Segment,
    dest: Path,
    settings: RenderSettings,
    camera: CameraPath,
    zoom: Callable[[float], float] | None = None,
) -> Path:
    """Passada 1 com reenquadramento: FFmpeg decodifica → OpenCV recorta → FFmpeg codifica.

    O decodificador entrega exatamente os frames da grade de fps do trecho (mesmo
    seek preciso do caminho sem reenquadramento); cada frame k corresponde a
    t_src = inicio + k/fps, e a janela vem de `camera.window(t_src)`. O resultado
    tem o mesmo contrato de `_render_segment`: N frames + N/fps s de PCM.

    Com `zoom`, a escala do frame k é `zoom(t_out + k/fps)`; num trecho que tem
    zoom, todos os frames usam a janela fracionária (sem salto na entrada do zoom).
    """
    ow, oh, fps = settings.width, settings.height, settings.fps
    n = seg.frames
    escalas = [zoom(seg.t_out + k / fps) if zoom else 1.0 for k in range(n)]
    com_zoom = any(e > 1.0 + 1e-9 for e in escalas)
    # Fonte muito maior que a saída (4K): reduz o quadro no FFmpeg, antes do pipe,
    # até a janela mais fechada ainda cobrir a largura de saída. Menos bytes por
    # frame e menos trabalho do OpenCV, sem perder detalhe do recorte.
    reducao = min(1.0, ow / (camera.cw / max(escalas)))
    sw, sh = _par(camera.largura * reducao), _par(camera.altura * reducao)
    fx, fy = sw / camera.largura, sh / camera.altura
    frame_bytes = sw * sh * 3

    dec_cmd = [*FFMPEG_BASE, "-ss", f"{seg.inicio:.6f}", "-i", str(seg.arquivo)]
    cor = _decode_color_filter(seg.arquivo, camera.altura)
    vf_dec = f"fps={fps},{cor},scale={sw}:{sh}"
    dec_cmd += ["-map", "0:v:0", "-vf", vf_dec, "-frames:v", str(n)]
    dec_cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"]

    entradas, audio_map, af = _audio_args(seg, settings, 1)
    enc_cmd = [*FFMPEG_BASE, "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{ow}x{oh}"]
    enc_cmd += ["-r", str(fps), "-i", "-", *entradas]
    enc_cmd += [
        "-map", "0:v:0", "-map", audio_map, "-af", af,
        "-frames:v", str(n), "-t", f"{n / fps:.6f}",
        # saída HD: BT.709 limitado, com as marcações (sem elas o player adivinha errado)
        "-vf",
        "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
        # marca os frames: as opções -color_* abaixo sozinhas não chegam ao bitstream
        "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
        "-r", str(fps), "-c:a", "pcm_s16le", "-ar", str(settings.sample_rate), "-ac", "2",
        str(dest),
    ]  # fmt: skip

    what = f"trecho {seg.indice} ({seg.arquivo.name} [{seg.inicio:.3f}, {seg.fim:.3f}])"
    with tempfile.TemporaryFile() as dec_err, tempfile.TemporaryFile() as enc_err:
        try:
            dec = subprocess.Popen(dec_cmd, stdout=subprocess.PIPE, stderr=dec_err)
            enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=enc_err)
        except FileNotFoundError as exc:
            raise RenderError("ffmpeg não encontrado no PATH") from exc
        assert dec.stdout is not None and enc.stdin is not None
        ultimo: np.ndarray | None = None
        try:
            for k in range(n):
                buf = dec.stdout.read(frame_bytes)
                if len(buf) == frame_bytes:
                    ultimo = np.frombuffer(buf, np.uint8).reshape(sh, sw, 3)
                elif ultimo is None:
                    raise RenderError(f"nenhum frame decodificado em {what}")
                # (fim do arquivo antes do fim do trecho: repete o último frame)
                t_src = seg.inicio + k / fps
                if com_zoom or reducao < 1.0:
                    x, y, cw, ch = camera.window_f(t_src, escalas[k])
                    janela = (x * fx, y * fy, cw * fx, ch * fy)
                    quadro = _crop_subpixel(ultimo, janela, ow, oh)
                else:
                    x, y, cw, ch = camera.window(t_src)
                    quadro = _crop(ultimo, x, y, cw, ch, ow, oh)
                enc.stdin.write(quadro.tobytes())
            enc.stdin.close()
        except BrokenPipeError:
            pass  # o encoder morreu; o erro dele é relatado abaixo
        except BaseException:
            for proc in (dec, enc):
                proc.kill()
            raise
        finally:
            dec.stdout.close()
        dec.kill()  # já leu o que precisava; pode haver frames sobrando
        dec.wait()
        if enc.wait() != 0:
            enc_err.seek(0)
            tail = enc_err.read().decode("utf-8", "replace").strip().splitlines()
            raise RenderError(
                f"FFmpeg falhou ao codificar {what}:\n" + "\n".join(tail[-STDERR_TAIL_LINES:])
            )
    return dest


def _par(v: float) -> int:
    """Dimensão par e >= 2 (o libx264 e o pipe exigem)."""
    return max(2, int(round(v / 2)) * 2)


def _crop(frame: np.ndarray, x: int, y: int, cw: int, ch: int, ow: int, oh: int) -> np.ndarray:
    recorte = frame[y : y + ch, x : x + cw]
    # ampliar com bicúbica (+5 dB PSNR que a linear, ~0,5 ms/frame a mais)
    interp = cv2.INTER_AREA if cw > ow else cv2.INTER_CUBIC
    return cv2.resize(recorte, (ow, oh), interpolation=interp)


def _crop_subpixel(
    frame: np.ndarray, janela: tuple[float, float, float, float], ow: int, oh: int
) -> np.ndarray:
    """Recorta a janela fracionária (x, y, w, h) e a leva para ow x oh.

    Ampliação: uma transformação afim bicúbica com a posição exata (sem tremor).
    Redução (fonte grande): recorte inteiro + INTER_AREA, sem serrilhado; aí o erro
    de arredondamento é menor que 1 px da saída.
    """
    x, y, w, h = janela
    if w > ow:
        xi, yi = int(round(x)), int(round(y))
        return _crop(frame, xi, yi, int(round(w)), int(round(h)), ow, oh)
    sx, sy = ow / w, oh / h
    # centro de pixel: saída (u+0,5)/s = entrada (x' - x + 0,5)
    m = np.array(
        [[sx, 0.0, -x * sx + 0.5 * (sx - 1)], [0.0, sy, -y * sy + 0.5 * (sy - 1)]],
        dtype=np.float64,
    )
    return cv2.warpAffine(
        frame, m, (ow, oh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


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
    concatenated: Path,
    tmp: Path,
    timeline: Timeline,
    settings: RenderSettings,
    legendas: Path | None = None,
    overlays: Sequence[Overlay] = (),
) -> Path:
    """Passada 2: efeitos sobre o vídeo concatenado, já no tempo do vídeo final.

    - Etapa 8: imagens (`overlays`), cada uma com `enable='between(t,a,b)'`, fade de
      entrada/saída e um leve zoom de entrada (92% → 100% em 0,25 s).
    - Etapa 7: legendas `.ass` com o filtro `ass=`, por cima das imagens.
    Os intervalos já chegam dentro dos clipes (não atravessam emendas).
    Sem efeitos, devolve o concatenado.
    """
    if legendas is None and not overlays:
        return concatenated
    # Filtros com caminhos do Windows (dois-pontos, barras, espaços) são frágeis:
    # tudo roda com cwd na pasta de trabalho, com nomes relativos.
    cmd = [*FFMPEG_BASE, "-i", concatenated.name]
    grafo: list[str] = []
    atual = "0:v"
    fps = settings.fps
    for k, ov in enumerate(overlays, start=1):
        nome = f"overlay_{k}.png"
        shutil.copyfile(ov.png, tmp / nome)
        dur = max(ov.fim - ov.inicio, 1 / fps)
        fade = min(0.2, dur / 3)
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", nome]
        cx, cy = ov.x + ov.w / 2, ov.y + ov.h / 2
        grafo.append(
            f"[{k}:v]format=rgba,"
            f"scale=w='trunc(iw*min(1,0.92+0.08*t/0.25)/2)*2':h=-2:eval=frame,"
            f"fade=t=in:st=0:d={fade:.3f}:alpha=1,"
            f"fade=t=out:st={dur - fade:.3f}:d={fade:.3f}:alpha=1,"
            f"setpts=PTS-STARTPTS+{ov.inicio:.4f}/TB[img{k}]"
        )
        grafo.append(
            f"[{atual}][img{k}]overlay=x='{cx:.1f}-overlay_w/2':y='{cy:.1f}-overlay_h/2':"
            f"enable='between(t,{ov.inicio:.4f},{ov.fim:.4f})':eof_action=pass[v{k}]"
        )
        atual = f"v{k}"
    finais = []
    if legendas is not None:
        from src.captions import FONTS_DIR

        shutil.copyfile(legendas, tmp / "legendas.ass")
        fontes = tmp / "fonts"
        fontes.mkdir(exist_ok=True)
        for fonte in FONTS_DIR.glob("*.[ot]tf"):
            shutil.copyfile(fonte, fontes / fonte.name)
        finais.append("ass=legendas.ass:fontsdir=fonts")
    finais.append("setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv")
    grafo.append(f"[{atual}]{','.join(finais)}[out]")

    saida = tmp / "passada2.mp4"
    cmd += ["-filter_complex", ";".join(grafo), "-map", "[out]", "-map", "0:a?"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "copy", "-movflags", "+faststart", saida.name]
    _run_ffmpeg(cmd, "passada 2 (imagens/legendas)", cwd=tmp)
    return saida


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


def _run_ffmpeg(cmd: list[str], what: str, cwd: Path | None = None) -> None:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd
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
