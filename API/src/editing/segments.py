"""Divide o tempo final em partes cacheáveis sem atravessar emendas."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from src.editing.project_schema import DocumentoEdicao
from src.render import Segment

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.broll.transitions import Cutaway, Transition
    from src.images import Overlay


@dataclass(frozen=True)
class SegmentoEdicao:
    """Intervalo de quadros finais e os IDs que podem afetá-lo."""

    render: Segment
    ids: tuple[str, ...]

    @property
    def inicio_frame(self) -> int:
        return round(self.render.t_out * self.render_fps)

    @property
    def fim(self) -> float:
        return self.render.t_out + self.render.frames / self.render_fps

    # fps é preenchido pelo planejador; não entra em `Segment` de render legado.
    render_fps: int = 30


def plan_edit_segments(
    base: list[Segment],
    documento: DocumentoEdicao,
    fps: int,
    *,
    overlays: Sequence[Overlay] = (),
    broll: Sequence[Cutaway] = (),
    broll_transition: Transition = "hard_cut",
    cameras_ativas: bool = False,
    zoom_ativo: bool = False,
    zooms_visiveis: frozenset[str] | None = None,
    destaques_ativos: bool = False,
    legendas_ids: frozenset[str] = frozenset(),
    max_gap: float = 3.0,
) -> list[SegmentoEdicao]:
    """Une efeitos sobrepostos e fatia lacunas longas em até `max_gap`."""
    if fps <= 0 or max_gap <= 0:
        raise ValueError("fps e max_gap precisam ser positivos")
    overlay_ids = {f"img_{item.item_id:03d}" for item in overlays}
    broll_ranges = [(round(c.inicio * fps), round(c.fim * fps)) for c in broll]
    gap_frames = max(1, round(max_gap * fps))
    trans_frames = round(0.25 * fps) if broll_transition != "hard_cut" else 0
    guard_frames = max(1, trans_frames)
    ids_visiveis: set[str] = set()
    for elemento in documento.elementos:
        if not elemento.ativo or elemento.obsoleto:
            continue
        if elemento.tipo in {"clipe", "corte"}:
            ids_visiveis.add(elemento.id)
        elif elemento.tipo == "crop" and cameras_ativas:
            ids_visiveis.add(elemento.id)
        elif elemento.tipo == "imagem" and elemento.id in overlay_ids:
            ids_visiveis.add(elemento.id)
        elif elemento.tipo == "zoom" and zoom_ativo and (
            zooms_visiveis is None or elemento.id in zooms_visiveis
        ):
            ids_visiveis.add(elemento.id)
        elif elemento.tipo == "broll" and any(
            round(elemento.inicio * fps) == inicio and round(elemento.fim * fps) == fim
            for inicio, fim in broll_ranges
        ):
            ids_visiveis.add(elemento.id)
        elif elemento.tipo == "destaque" and destaques_ativos:
            ids_visiveis.add(elemento.id)
        elif elemento.tipo == "legenda" and elemento.id in legendas_ids:
            ids_visiveis.add(elemento.id)
    planejados: list[SegmentoEdicao] = []
    for original in base:
        a = round(original.t_out * fps)
        b = a + original.frames
        ocupados: list[tuple[int, int]] = []
        for elemento in documento.elementos:
            if elemento.id not in ids_visiveis or elemento.tipo not in {
                "imagem", "zoom", "broll", "destaque"
            }:
                continue
            inicio = round(elemento.inicio * fps)
            fim = round(elemento.fim * fps)
            if elemento.tipo == "broll":
                inicio -= guard_frames
                fim += guard_frames
            inicio, fim = max(a, inicio), min(b, fim)
            if fim > inicio:
                ocupados.append((inicio, fim))
        for cut in broll:
            inicio = max(a, round(cut.inicio * fps) - guard_frames)
            fim = min(b, round(cut.fim * fps) + guard_frames)
            if fim > inicio:
                ocupados.append((inicio, fim))
        for ov in overlays:
            inicio, fim = max(a, round(ov.inicio * fps)), min(b, round(ov.fim * fps))
            if fim > inicio:
                ocupados.append((inicio, fim))
        ocupados.sort()
        unidos: list[tuple[int, int]] = []
        for inicio, fim in ocupados:
            if unidos and inicio <= unidos[-1][1]:
                unidos[-1] = (unidos[-1][0], max(unidos[-1][1], fim))
            else:
                unidos.append((inicio, fim))
        ranges: list[tuple[int, int]] = []
        cursor = a
        for inicio, fim in unidos:
            while cursor < inicio:
                proximo = min(cursor + gap_frames, inicio)
                ranges.append((cursor, proximo))
                cursor = proximo
            ranges.append((inicio, fim))
            cursor = fim
        while cursor < b:
            proximo = min(cursor + gap_frames, b)
            ranges.append((cursor, proximo))
            cursor = proximo
        for inicio, fim in ranges:
            ids = tuple(
                sorted(
                    elemento.id
                    for elemento in documento.elementos
                    if elemento.id in ids_visiveis
                    and round(elemento.inicio * fps) < fim
                    and inicio < round(elemento.fim * fps)
                )
            )
            local_start = inicio - a
            local_end = fim - a
            render = replace(
                original,
                indice=len(planejados),
                inicio=original.inicio + local_start / fps,
                fim=original.inicio + local_end / fps,
                frames=fim - inicio,
                t_out=inicio / fps,
                fade_in=original.fade_in and local_start == 0,
                fade_out=original.fade_out and local_end == original.frames,
            )
            planejados.append(SegmentoEdicao(render, ids, fps))
    return planejados
