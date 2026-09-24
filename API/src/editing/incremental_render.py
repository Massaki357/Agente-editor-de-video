"""Render por segmentos finais com cache de vídeo e remontagem sem recodificar imagem."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from src.cache import atomic_write_text, file_hash
from src.editing.project_schema import DocumentoEdicao, eventos_ass
from src.editing.segments import SegmentoEdicao, plan_edit_segments
from src.project import Timeline
from src.render import (
    RenderError,
    RenderSettings,
    _clip_meta,
    _concat,
    _output_size,
    _render_segment,
    _render_segment_reframe,
    _second_pass,
    plan_segments,
)

if TYPE_CHECKING:
    from src.broll.transitions import Cutaway, Transition
    from src.images import Overlay
    from src.reframe import CameraPath

CACHE_VERSION = 1


@dataclass(frozen=True)
class IncrementalResult:
    output: Path
    segmentos: int
    renderizados: int
    reutilizados: int
    frames_renderizados: int
    total_frames: int


def _overlap(a: float, b: float, c: float, d: float) -> bool:
    return a < d - 1e-9 and c < b - 1e-9


def _hash_segment(
    planned: SegmentoEdicao,
    documento: DocumentoEdicao,
    settings: RenderSettings,
    cameras: Mapping[int, CameraPath],
    zoom: Callable[[float], float] | None,
    legendas: Path | None,
    overlays: Sequence[Overlay],
    broll: Sequence[Cutaway],
    transition: Transition,
) -> str:
    seg = planned.render
    end = seg.t_out + seg.frames / settings.fps
    camera = cameras.get(seg.clip_index)
    # Eventos e estilo só invalidam o trecho em que a legenda aparece.
    if legendas is not None:
        ass_text = legendas.read_text(encoding="utf-8-sig")
        ass_events = [
            e.model_dump(mode="json")
            for e in eventos_ass(legendas)
            if _overlap(e.inicio, e.fim, seg.t_out, end)
        ]
        ass_style = (
            hashlib.sha256(ass_text.split("[Events]", 1)[0].encode()).hexdigest()
            if ass_events else None
        )
        if ass_events:
            from src.captions import FONTS_DIR

            fontes = [
                (fonte.name, file_hash(fonte)) for fonte in sorted(FONTS_DIR.glob("*.[ot]tf"))
            ]
        else:
            fontes = []
    else:
        ass_style, ass_events, fontes = None, [], []
    frames_camera = (
        [
            tuple(round(v, 5) for v in camera.window_f(seg.inicio + k / settings.fps))
            for k in range(seg.frames)
        ]
        if camera is not None
        else None
    )
    escalas = (
        [round(zoom(seg.t_out + k / settings.fps), 6) for k in range(seg.frames)]
        if zoom is not None and camera is not None
        else None
    )
    dados = {
        "v": CACHE_VERSION,
        "fonte": file_hash(seg.arquivo),
        "audio": file_hash(seg.audio) if seg.audio else None,
        "seg": [
            seg.inicio, seg.fim, seg.frames, seg.t_out, seg.clip_index,
            seg.tem_audio, seg.fade_in, seg.fade_out,
        ],
        "render": [
            settings.width, settings.height, settings.fps, settings.sample_rate, settings.fade
        ],
        "camera": frames_camera,
        "zoom": escalas,
        "elementos": [
            e.model_dump(mode="json")
            for e in documento.elementos
            if e.id in planned.ids and not e.obsoleto
        ],
        "ass_style": ass_style,
        "ass_events": ass_events,
        "fontes": fontes,
        "overlays": [
            [ov.item_id, ov.x, ov.y, ov.w, ov.h, ov.inicio, ov.fim, file_hash(ov.png)]
            for ov in overlays
            if _overlap(ov.inicio, ov.fim, seg.t_out, end)
        ],
        "broll": [
            [c.inicio, c.fim, c.clipe, file_hash(c.arquivo)]
            for c in broll
            if _overlap(c.inicio, c.fim, seg.t_out, end)
        ],
        "transicao": transition if any(
            _overlap(c.inicio, c.fim, seg.t_out, end) for c in broll
        ) else None,
    }
    return hashlib.sha256(
        json.dumps(dados, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _render_one(
    planned: SegmentoEdicao,
    dest: Path,
    settings: RenderSettings,
    cameras: Mapping[int, CameraPath],
    zoom: Callable[[float], float] | None,
    legendas: Path | None,
    overlays: Sequence[Overlay],
    broll: Sequence[Cutaway],
    transition: Transition,
) -> None:
    from src.broll.transitions import Cutaway, apply_cutaways

    seg = planned.render
    end = seg.t_out + seg.frames / settings.fps
    with tempfile.TemporaryDirectory(prefix="trecho_edicao_", dir=dest.parent) as work:
        tmp = Path(work)
        base = tmp / "base.mov"
        camera = cameras.get(seg.clip_index)
        if camera is None:
            _render_segment(seg, base, settings)
        else:
            _render_segment_reframe(seg, base, settings, camera, zoom)
        locais = [c for c in broll if _overlap(c.inicio, c.fim, seg.t_out, end)]
        if locais:
            if any(c.inicio < seg.t_out - 1e-6 or c.fim >= end - 1e-6 for c in locais):
                raise RenderError("segmento não contém o B-roll e seu retorno à câmera")
            shifted = [
                Cutaway(c.inicio - seg.t_out, c.fim - seg.t_out, c.arquivo, c.clipe)
                for c in locais
            ]
            base = apply_cutaways(
                base,
                tmp / "broll.mov",
                shifted,
                [replace(seg, t_out=0.0)],
                settings.fps,
                transition=transition,
            )
        visible_overlays = [
            ov for ov in overlays if _overlap(ov.inicio, ov.fim, seg.t_out, end)
        ]
        legendas_locais = (
            legendas is not None and any(
                _overlap(e.inicio, e.fim, seg.t_out, end)
                for e in eventos_ass(legendas)
            )
        )
        if legendas_locais or visible_overlays:
            base = _second_pass(
                base, tmp, Timeline(), settings,
                legendas if legendas_locais else None, visible_overlays,
                time_offset=seg.t_out, output_ext=".mov",
            )
        os.replace(base, dest)


def render_incremental(
    timeline: Timeline,
    documento: DocumentoEdicao,
    output: Path,
    cache_dir: Path,
    *,
    size: tuple[int, int] | None = None,
    fps: int = 30,
    sample_rate: int = 48_000,
    fade: float = 0.025,
    cameras: Mapping[int, CameraPath] | None = None,
    zoom: Callable[[float], float] | None = None,
    legendas: Path | None = None,
    overlays: Sequence[Overlay] = (),
    broll: Sequence[Cutaway] = (),
    broll_transition: Transition = "hard_cut",
    audios: Mapping[int, Path] | None = None,
    ids_alterados: set[str] | None = None,
    force_full: bool = False,
    max_gap: float = 3.0,
    progress: Callable[[int, int], None] | None = None,
) -> IncrementalResult:
    """Só renderiza segmentos sem cache ou tocados pelos IDs alterados."""
    output, cache_dir = Path(output), Path(cache_dir)
    metas = [_clip_meta(clip.arquivo, clip.meta) for clip in timeline.clipes]
    base = plan_segments(timeline, metas, fps, audios)
    if not base:
        raise RenderError("timeline sem trechos para renderizar")
    if broll:
        from src.broll.transitions import validate_cutaways

        validate_cutaways(broll, base, fps, broll_transition, 0.25)
    width, height = _output_size(size, metas[base[0].clip_index])
    settings = RenderSettings(width, height, fps, sample_rate, fade)
    legendas_eventos = eventos_ass(legendas) if legendas is not None else []
    destaque_ass = (
        legendas is not None
        and "Style: Destaque" in legendas.read_text(encoding="utf-8-sig")
    )
    zooms_visiveis = frozenset(
        e.id for e in documento.elementos
        if e.tipo == "zoom" and zoom is not None and cameras
        and any(
            zoom(k / fps) > 1.0 + 1e-9
            for k in range(round(e.inicio * fps), round(e.fim * fps))
        )
    )
    planned = plan_edit_segments(
        base, documento, fps, overlays=overlays, broll=broll,
        broll_transition=broll_transition, cameras_ativas=bool(cameras),
        zoom_ativo=zoom is not None and bool(cameras), zooms_visiveis=zooms_visiveis,
        destaques_ativos=destaque_ass,
        legendas_ids=frozenset(e.id for e in legendas_eventos), max_gap=max_gap,
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / "manifest.json"
    try:
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {"segmentos": []}
    changed = ids_alterados or set()
    old_ranges = [
        (s["inicio_frame"], s["fim_frame"])
        for s in old.get("segmentos", [])
        if changed.intersection(s.get("ids", []))
    ]
    files: list[Path] = []
    manifest = []
    rendered = 0
    rendered_frames = 0
    total = len(planned) + 1
    for i, item in enumerate(planned):
        seg = item.render
        inicio = round(seg.t_out * fps)
        fim = inicio + seg.frames
        key = _hash_segment(
            item, documento, settings, cameras or {}, zoom, legendas, overlays, broll,
            broll_transition,
        )
        dest = cache_dir / f"{key}.mov"
        forced = force_full or bool(changed.intersection(item.ids)) or any(
            a < fim and inicio < b for a, b in old_ranges
        )
        if forced or not dest.is_file():
            _render_one(
                item, dest, settings, cameras or {}, zoom, legendas, overlays, broll,
                broll_transition,
            )
            rendered += 1
            rendered_frames += seg.frames
        files.append(dest)
        manifest.append(
            {"inicio_frame": inicio, "fim_frame": fim, "ids": list(item.ids), "hash": key}
        )
        if progress:
            progress(i + 1, total)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="montagem_", dir=output.parent) as work:
        tmp = Path(work) / "final.mp4"
        _concat(files, [item.render for item in planned], tmp, settings)
        os.replace(tmp, output)
    atomic_write_text(manifest_path, json.dumps({"segmentos": manifest}, indent=2))
    if progress:
        progress(total, total)
    return IncrementalResult(
        output, len(planned), rendered, len(planned) - rendered, rendered_frames,
        sum(item.render.frames for item in planned),
    )
