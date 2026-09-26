"""Substitui trechos do vídeo por B-roll, preservando a trilha de voz inteira."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from src.broll.catalog import Choice, Direction, choose, installed_xfade_effects
from src.broll.planner import ItemBroll
from src.broll.source import prepare_item
from src.config import Settings

if TYPE_CHECKING:
    from src.render import Segment

log = logging.getLogger(__name__)
Transition = Literal["hard_cut", "crossfade", "slide", "wipe", "reveal", "zoom", "blur"]


@dataclass(frozen=True)
class TransitionConfig:
    """Preset e parâmetros opcionais de uma borda do cutaway."""

    preset: Transition
    duration: float | None = None
    direction: Direction | None = None
    intensity: float | None = None

    def resolve(self, fps: int) -> Choice:
        return choose(self.preset, fps=fps, duration=self.duration,
                      direction=self.direction, intensity=self.intensity)


@dataclass(frozen=True)
class Cutaway:
    """Vídeo normalizado e efeitos independentes na entrada e no retorno."""

    inicio: float
    fim: float
    arquivo: Path
    clipe: int
    entrada: TransitionConfig | None = None
    saida: TransitionConfig | None = None


@dataclass(frozen=True)
class _Planned:
    start: int
    end: int
    path: Path
    entry: Choice
    exit: Choice


def _default(transition: Transition, fade: float) -> TransitionConfig:
    # Preserva a duração histórica para os quatro efeitos existentes.
    return TransitionConfig(transition, fade if transition in {
        "crossfade", "slide", "wipe"
    } else None)


def select_items(
    items: Sequence[ItemBroll], images: Sequence[tuple[float, float]],
    intervalo_min: float = 8.0,
) -> list[ItemBroll]:
    """Seleciona aprovados respeitando imagens, densidade e retorno à câmera."""
    chosen: list[ItemBroll] = []
    for item in sorted(items, key=lambda value: value.inicio):
        if not item.ativo or not item.aprovado:
            continue
        if any(item.inicio < fim and inicio < item.fim for inicio, fim in images):
            log.info("B-roll '%s' pulado por sobreposição com imagem.", item.query)
            continue
        if chosen and (
            item.inicio - chosen[-1].inicio < intervalo_min or item.inicio - chosen[-1].fim < 1.0
        ):
            log.info("B-roll '%s' pulado pelo limite de densidade.", item.query)
            continue
        chosen.append(item)
    return chosen


def prepare_cutaways(items: Sequence[ItemBroll], settings: Settings) -> list[Cutaway]:
    """Busca só itens ativos; sem resultado, mantém a câmera naquele intervalo."""
    cutaways = []
    for item in items:
        if not item.ativo or not item.aprovado:
            continue
        prepared = prepare_item(item, settings=settings)
        if prepared is None:
            log.warning("B-roll '%s' indisponível; mantendo a câmera.", item.query)
            continue
        cutaways.append(Cutaway(item.inicio, item.fim, prepared.arquivo, item.clipe))
    return cutaways


def _plan_cutaways(
    cutaways: Sequence[Cutaway], segments: Sequence[Segment], fps: int,
    transition: Transition, fade: float, warnings: list[str] | None = None,
) -> list[_Planned]:
    if not 0 < fade <= 0.5:
        raise ValueError("fade do B-roll precisa estar entre 0 e 0,5 s")
    default = _default(transition, fade)
    configured = [
        (cut, (cut.entrada or default).resolve(fps), (cut.saida or default).resolve(fps))
        for cut in sorted(cutaways, key=lambda item: item.inicio)
    ]
    needed = {effect for _, entry, exit_choice in configured
              for effect in (entry.entrada, exit_choice.saida) if effect}
    try:
        available = installed_xfade_effects() if needed else set()
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning("Não foi possível consultar os efeitos xfade: %s", exc)
        available = set()
    hard_cut = choose("hard_cut", fps=fps)
    total = sum(seg.frames for seg in segments)
    result: list[_Planned] = []
    for cut, entry, exit_choice in configured:
        for side, effect in (("entrada", entry.entrada), ("saída", exit_choice.saida)):
            if effect and effect not in available:
                message = (f"Efeito de transição '{effect}' indisponível no FFmpeg; "
                           f"{side} do B-roll em {cut.inicio:.2f}s usa corte seco.")
                log.warning(message)
                if warnings is not None and message not in warnings:
                    warnings.append(message)
                if side == "entrada":
                    entry = hard_cut
                else:
                    exit_choice = hard_cut
        start, end = round(cut.inicio * fps), round(cut.fim * fps)
        if start < 0 or end <= start or end >= total:
            raise ValueError("B-roll fora da timeline ou sem retorno à câmera")
        if result and start <= result[-1].end:
            raise ValueError("cutaways sobrepostos ou sem retorno à câmera")
        containing = next((seg for seg in segments
                           if seg.clip_index == cut.clipe
                           and start >= round(seg.t_out * fps)
                           and end < round(seg.t_out * fps) + seg.frames), None)
        if containing is None:
            raise ValueError("B-roll atravessa uma emenda entre trechos ou clipes")
        seg_start = round(containing.t_out * fps)
        seg_end = seg_start + containing.frames
        if (start - seg_start < entry.frames or seg_end - end < exit_choice.frames
                or end - start <= entry.frames + exit_choice.frames):
            raise ValueError("cutaway curto demais para a transição")
        if result and start - result[-1].end < result[-1].exit.frames + entry.frames + 1:
            raise ValueError("intervalo entre cutaways curto demais para a transição")
        if not cut.arquivo.is_file():
            raise ValueError(f"arquivo de B-roll ausente: {cut.arquivo}")
        result.append(_Planned(start, end, cut.arquivo, entry, exit_choice))
    return result


def validate_cutaways(
    cutaways: Sequence[Cutaway], segments: Sequence[Segment], fps: int,
    transition: Transition, fade: float, warnings: list[str] | None = None,
) -> list[tuple[int, int, Path]]:
    """Converte para frames e impede sobreposição ou travessia de uma emenda."""
    return [(p.start, p.end, p.path)
            for p in _plan_cutaways(cutaways, segments, fps, transition, fade, warnings)]


def apply_cutaways(
    camera: Path, output: Path, cutaways: Sequence[Cutaway],
    segments: Sequence[Segment], fps: int, *, transition: Transition = "hard_cut",
    fade: float = 0.25, warnings: list[str] | None = None,
) -> Path:
    """Monta transições independentes e copia o áudio AAC sem recodificação."""
    from src.render import FFMPEG_BASE, _run_ffmpeg

    planned = _plan_cutaways(cutaways, segments, fps, transition, fade, warnings)
    if not planned:
        return camera
    total = sum(seg.frames for seg in segments)
    parts: list[tuple[int, int, int]] = []
    edges: list[tuple[int, str | None]] = []
    previous_end = 0
    previous_exit = 0
    for index, item in enumerate(planned, start=1):
        parts.append((0, previous_end - previous_exit, item.start + item.entry.frames))
        if index > 1:
            edges.append((previous_exit, planned[index - 2].exit.saida))
        parts.append((index, 0, item.end - item.start))
        edges.append((item.entry.frames, item.entry.entrada))
        previous_end, previous_exit = item.end, item.exit.frames
    parts.append((0, previous_end - previous_exit, total))
    edges.append((previous_exit, planned[-1].exit.saida))

    cmd = [*FFMPEG_BASE, "-i", str(camera)]
    cmd.extend(arg for item in planned for arg in ("-i", str(item.path)))
    graph = [
        f"[{input_index}:v]trim=start_frame={start}:end_frame={end},"
        f"setpts=PTS-STARTPTS,settb=AVTB,format=yuv420p[p{index}]"
        for index, (input_index, start, end) in enumerate(parts)
    ]
    elapsed = parts[0][2] - parts[0][1]
    last = "p0"
    for index, ((frames, effect), (_, start, end)) in enumerate(
        zip(edges, parts[1:], strict=True), start=1
    ):
        target = "v" if index == len(edges) else f"x{index}"
        if frames:
            graph.append(
                f"[{last}][p{index}]xfade=transition={effect}:duration={frames / fps:.6f}:"
                f"offset={(elapsed - frames) / fps:.6f}[{target}]"
            )
        else:
            graph.append(f"[{last}][p{index}]concat=n=2:v=1:a=0[{target}]")
        elapsed += end - start - frames
        last = target
    if elapsed != total:
        raise ValueError("transição alterou a contagem de quadros planejada")
    cmd += [
        "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "0:a:0",
        "-frames:v", str(total), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "copy", "-movflags", "+faststart", str(output),
    ]
    _run_ffmpeg(cmd, "cutaways de B-roll")
    return output
