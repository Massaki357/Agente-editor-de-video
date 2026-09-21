"""Listar e ordenar clipes (natsort) e ler metadados via ffprobe."""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Iterable
from fractions import Fraction
from pathlib import Path

from natsort import natsorted, ns

from src.cache import file_hash, read_json_cache, write_json_cache
from src.project import Clip, ClipMeta, Project, Timeline

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv"}


class ClipProbeError(RuntimeError):
    """ffprobe falhou ou o arquivo não tem stream de vídeo."""


def list_clips_from_folder(path: str | Path) -> list[Path]:
    """Vídeos da pasta (não recursivo) em ordem natural: 1, 2, 10 — não 1, 10, 2."""
    folder = Path(path)
    if not folder.is_dir():
        raise NotADirectoryError(f"pasta não encontrada: {folder}")
    files = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS and not p.name.startswith(".")
    ]
    return natsorted(files, alg=ns.PATH | ns.IGNORECASE)


def probe_clip(path: str | Path) -> ClipMeta:
    """Duração, resolução de exibição, fps e presença de áudio via ffprobe (em cache)."""
    path = Path(path)
    key = file_hash(path)
    cached = read_json_cache("probe", key)
    if cached is not None:
        return ClipMeta.model_validate(cached)

    meta = _parse_ffprobe(_run_ffprobe(path), path)
    write_json_cache("probe", key, meta.model_dump())
    return meta


def _run_ffprobe(path: Path) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams"]
    try:
        proc = subprocess.run(
            [*cmd, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise ClipProbeError(
            "ffprobe não encontrado no PATH (rode `python -m src.doctor`)"
        ) from exc
    if proc.returncode != 0:
        raise ClipProbeError(f"ffprobe falhou em {path.name}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def _parse_ffprobe(info: dict, path: Path) -> ClipMeta:
    streams = info.get("streams", [])
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise ClipProbeError(f"{path.name} não tem stream de vídeo")

    duracao = _to_float(info.get("format", {}).get("duration")) or _to_float(video.get("duration"))
    if not duracao:
        raise ClipProbeError(f"{path.name}: duração desconhecida")

    rotacao = _rotation(video)
    largura, altura = int(video["width"]), int(video["height"])
    if rotacao % 180:
        largura, altura = altura, largura

    return ClipMeta(
        duracao=duracao,
        largura=largura,
        altura=altura,
        fps=_fps(video),
        tem_audio=any(s.get("codec_type") == "audio" for s in streams),
        rotacao=rotacao,
    )


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _fps(video: dict) -> float:
    for field in ("avg_frame_rate", "r_frame_rate"):
        try:
            fps = Fraction(video.get(field, "0/0"))
        except (ValueError, ZeroDivisionError):
            continue
        if fps > 0:
            return round(float(fps), 3)
    return 0.0


def _rotation(video: dict) -> int:
    """Rotação de celular: `tags.rotate` (ffprobe antigo) ou `side_data_list[].rotation`."""
    raw = video.get("tags", {}).get("rotate")
    if raw is None:
        raw = next(
            (sd["rotation"] for sd in video.get("side_data_list", []) if "rotation" in sd), 0
        )
    try:
        return int(float(raw)) % 360
    except (TypeError, ValueError):
        return 0


def clip_from_file(path: str | Path) -> Clip:
    """Clipe inteiro (um trecho de 0 até a duração) com metadados."""
    path = Path(path).resolve()
    meta = probe_clip(path)
    return Clip(arquivo=str(path), trechos=[(0.0, meta.duracao)], meta=meta)


def project_from_files(paths: Iterable[str | Path]) -> Project:
    """Projeto na ordem dada (ex.: ordem de seleção no app). Offsets já calculados."""
    timeline = Timeline(clipes=[clip_from_file(p) for p in paths])
    timeline.recalcular_offsets()
    return Project(timeline=timeline)


def project_from_folder(path: str | Path) -> Project:
    """Projeto com os vídeos da pasta em ordem natural."""
    clips = list_clips_from_folder(path)
    if not clips:
        log.warning("Nenhum vídeo (%s) em %s", ", ".join(sorted(VIDEO_EXTENSIONS)), path)
    return project_from_files(clips)
