"""Substitui trechos do vídeo por B-roll, preservando a trilha de voz inteira."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Sequence

from src.broll.planner import ItemBroll
from src.broll.source import prepare_item
from src.config import Settings

if TYPE_CHECKING:
    from src.render import Segment

log = logging.getLogger(__name__)
Transition = Literal["hard_cut", "crossfade"]


@dataclass(frozen=True)
class Cutaway:
    """Vídeo já normalizado, com intervalo em tempo da saída (`t_out`)."""

    inicio: float
    fim: float
    arquivo: Path
    clipe: int


def prepare_cutaways(items: Sequence[ItemBroll], settings: Settings) -> list[Cutaway]:
    """Busca só itens ativos; sem resultado, mantém a câmera naquele intervalo."""
    cutaways = []
    for item in items:
        prepared = prepare_item(item, settings=settings)
        if prepared is None:
            log.warning("B-roll '%s' indisponível; mantendo a câmera.", item.query)
            continue
        cutaways.append(Cutaway(item.inicio, item.fim, prepared.arquivo, item.clipe))
    return cutaways


def validate_cutaways(
    cutaways: Sequence[Cutaway], segments: Sequence[Segment], fps: int, transition: Transition,
    fade: float,
) -> list[tuple[int, int, Path]]:
    """Converte para frames e impede sobreposição ou travessia de uma emenda."""
    if transition not in ("hard_cut", "crossfade"):
        raise ValueError(f"transição de B-roll inválida: {transition}")
    if not 0 < fade <= 0.5:
        raise ValueError("fade do B-roll precisa estar entre 0 e 0,5 s")
    total = sum(seg.frames for seg in segments)
    result: list[tuple[int, int, Path]] = []
    for cut in sorted(cutaways, key=lambda item: item.inicio):
        start, end = round(cut.inicio * fps), round(cut.fim * fps)
        if start < 0 or end <= start or end >= total:
            raise ValueError("B-roll fora da timeline ou sem retorno à câmera")
        if result and start <= result[-1][1]:
            raise ValueError("cutaways sobrepostos ou sem retorno à câmera")
        if not any(
            seg.clip_index == cut.clipe
            and start >= round(seg.t_out * fps)
            and end <= round(seg.t_out * fps) + seg.frames
            for seg in segments
        ):
            raise ValueError("B-roll atravessa uma emenda entre trechos ou clipes")
        if not cut.arquivo.is_file():
            raise ValueError(f"arquivo de B-roll ausente: {cut.arquivo}")
        result.append((start, end, cut.arquivo))
    if transition == "crossfade":
        frames_fade = round(fade * fps)
        if frames_fade < 1 or any(end - start <= 2 * frames_fade for start, end, _ in result):
            raise ValueError("cutaway curto demais para o crossfade")
        if any(
            current[0] - previous[1] < 2 * frames_fade
            for previous, current in zip(result, result[1:])
        ):
            raise ValueError("intervalo entre cutaways curto demais para o crossfade")
    return result


def apply_cutaways(
    camera: Path,
    output: Path,
    cutaways: Sequence[Cutaway],
    segments: Sequence[Segment],
    fps: int,
    *,
    transition: Transition = "hard_cut",
    fade: float = 0.25,
) -> Path:
    """Monta a trilha de vídeo e copia o áudio AAC original sem recodificá-lo."""
    from src.render import FFMPEG_BASE, _run_ffmpeg

    validated = validate_cutaways(cutaways, segments, fps, transition, fade)
    if not validated:
        return camera
    total = sum(seg.frames for seg in segments)
    f = round(fade * fps) if transition == "crossfade" else 0
    # [câmera, B-roll, câmera, ...]; no fade as pontas da câmera se sobrepõem
    # ao B-roll, e xfade mantém exatamente o número original de quadros.
    parts: list[tuple[int, int, int]] = []
    previous_end = 0
    for index, (start, end, _) in enumerate(validated, start=1):
        camera_start = previous_end - (f if index > 1 else 0)
        if start + f > camera_start:
            parts.append((0, camera_start, start + f))
        parts.append((index, 0, end - start))
        previous_end = end
    parts.append((0, previous_end - f, total))

    cmd = [*FFMPEG_BASE, "-i", str(camera)]
    cmd.extend(arg for _, _, path in validated for arg in ("-i", str(path)))
    graph = []
    for index, (input_index, start, end) in enumerate(parts):
        graph.append(
            f"[{input_index}:v]trim=start_frame={start}:end_frame={end},"
            f"setpts=PTS-STARTPTS,settb=AVTB,format=yuv420p[p{index}]"
        )
    if transition == "hard_cut":
        joined = "".join(f"[p{i}]" for i in range(len(parts)))
        graph.append(f"{joined}concat=n={len(parts)}:v=1:a=0[v]")
    else:
        elapsed = parts[0][2] - parts[0][1]
        last = "p0"
        for index in range(1, len(parts)):
            offset = (elapsed - f) / fps
            target = "v" if index == len(parts) - 1 else f"x{index}"
            graph.append(
                f"[{last}][p{index}]xfade=transition=fade:duration={f / fps:.6f}:"
                f"offset={offset:.6f}[{target}]"
            )
            elapsed += parts[index][2] - parts[index][1] - f
            last = target
    cmd += [
        "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "0:a:0",
        "-frames:v", str(total), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "copy", "-movflags", "+faststart", str(output),
    ]
    _run_ffmpeg(cmd, "cutaways de B-roll")
    return output
