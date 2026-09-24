"""Busca, download e preparo de clipes de B-roll (Pexels Videos → Pixabay Videos).

O MP4 devolvido está pronto para a timeline final: vertical, fps fixo, sem áudio e
com a duração da frase planejada. Falhas de busca/download devolvem ``None`` para
que o editor mantenha a câmera ou uma imagem naquele trecho.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

import requests
from pydantic import BaseModel, Field, model_validator

from src.broll.planner import ItemBroll
from src.cache import cache_path, read_json_cache, write_json_cache
from src.clips import ClipProbeError, probe_clip
from src.config import Settings, get_settings

log = logging.getLogger(__name__)
SOURCE_VERSION = 2  # invalida resultados antigos escolhidos sem checar relevância
PIXABAY_CACHE_SECONDS = 24 * 60 * 60
SEARCH_STOPWORDS = {"a", "an", "and", "at", "for", "from", "in", "of", "on", "the", "to", "with"}


class BrollSourceParams(BaseModel):
    """Limites de download e formato do vídeo pronto para o render."""

    width: int = Field(1080, ge=2)
    height: int = Field(1920, ge=2)
    fps: int = Field(30, ge=1, le=120)
    max_source_dimension: int = Field(1920, ge=240)
    max_download_bytes: int = Field(60 * 1024 * 1024, ge=1)
    candidates: int = Field(8, ge=1, le=40)
    max_duration: float = Field(4.0, gt=0)

    @model_validator(mode="after")
    def _even_dimensions(self) -> BrollSourceParams:
        if self.width % 2 or self.height % 2:
            raise ValueError("largura e altura do B-roll precisam ser pares")
        return self


class VideoCandidate(BaseModel):
    fonte: Literal["pexels", "pixabay"]
    id: str
    url: str
    pagina: str
    autor: str = ""
    largura: int
    altura: int
    duracao: float


class PreparedBroll(BaseModel):
    arquivo: Path
    query: str
    duracao: float
    fonte: Literal["pexels", "pixabay"]
    id: str
    pagina: str
    autor: str = ""


def _key(query: str, duracao: float, params: BrollSourceParams) -> str:
    data = [
        SOURCE_VERSION,
        query.casefold().strip(),
        round(duracao * params.fps),
        params.width,
        params.height,
        params.fps,
        params.max_source_dimension,
    ]
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()[:32]


def _best_variant(variants: list[dict], max_dimension: int, *, pexels: bool) -> dict | None:
    """Maior MP4 disponível até o teto; ignora HLS, dimensões nulas e 4K."""
    eligible = []
    for variant in variants:
        width, height = variant.get("width"), variant.get("height")
        url = variant.get("link") if pexels else variant.get("url")
        if (
            not isinstance(width, int)
            or not isinstance(height, int)
            or width <= 0
            or height <= 0
            or max(width, height) > max_dimension
            or not isinstance(url, str)
            or not url.startswith("https://")
            or (pexels and variant.get("file_type") != "video/mp4")
        ):
            continue
        eligible.append(variant)
    return max(eligible, key=lambda v: v["width"] * v["height"], default=None)


def _terms(value: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", plain)
    stems = set()
    for word in words:
        if word in SEARCH_STOPWORDS:
            continue
        if word.endswith("ing") and len(word) > 5:
            word = word[:-3]
        elif word.endswith("ies") and len(word) > 4:
            word = word[:-3] + "y"
        elif word.endswith("s") and len(word) > 3:
            word = word[:-1]
        stems.add(word)
    return stems


def _relevance(query: str, description: str) -> int:
    """Conta termos da busca presentes no título/tags do provedor."""
    return len(_terms(query) & _terms(description))


def _is_relevant(query: str, description: str) -> bool:
    terms = _terms(query)
    return _relevance(query, description) >= min(2, len(terms)) if terms else False


def _pexels(query: str, duracao: float, params: BrollSourceParams, settings: Settings):
    if settings.pexels_api_key is None:
        return []
    response = requests.get(
        "https://api.pexels.com/v1/videos/search",
        params={"query": query, "per_page": params.candidates},
        headers={"Authorization": settings.pexels_api_key.get_secret_value()},
        timeout=20,
    )
    response.raise_for_status()
    ranked = []
    for video in response.json().get("videos", []):
        page = video.get("url") or ""
        relevance = _relevance(query, unquote(urlsplit(page).path))
        if not _is_relevant(query, unquote(urlsplit(page).path)):
            continue
        seconds = video.get("duration")
        if not isinstance(seconds, int | float) or seconds + 0.05 < duracao:
            continue
        choice = _best_variant(
            video.get("video_files", []), params.max_source_dimension, pexels=True
        )
        if choice is None:
            continue
        ranked.append(
            (
                relevance,
                VideoCandidate(
                    fonte="pexels",
                    id=str(video["id"]),
                    url=choice["link"],
                    pagina=page,
                    autor=video.get("user", {}).get("name", ""),
                    largura=choice["width"],
                    altura=choice["height"],
                    duracao=seconds,
                ),
            )
        )
    return [candidate for _, candidate in sorted(ranked, key=lambda x: x[0], reverse=True)]


def _pixabay(query: str, duracao: float, params: BrollSourceParams, settings: Settings):
    if settings.pixabay_api_key is None:
        return []
    search_key = hashlib.sha256(
        json.dumps([SOURCE_VERSION, query.casefold().strip(), params.candidates]).encode()
    ).hexdigest()[:32]
    cached = read_json_cache("broll_pixabay_search", search_key, settings.cache_dir)
    if isinstance(cached, dict) and time.time() - cached.get("at", 0) < PIXABAY_CACHE_SECONDS:
        payload = cached["payload"]
    else:
        response = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": settings.pixabay_api_key.get_secret_value(),
                "q": query,
                "video_type": "film",
                "safesearch": "true",
                "per_page": max(3, params.candidates),
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        write_json_cache(
            "broll_pixabay_search",
            search_key,
            {"at": time.time(), "payload": payload},
            settings.cache_dir,
        )
    ranked = []
    for video in payload.get("hits", []):
        relevance = _relevance(query, video.get("tags") or "")
        if not _is_relevant(query, video.get("tags") or ""):
            continue
        seconds = video.get("duration")
        if not isinstance(seconds, int | float) or seconds + 0.05 < duracao:
            continue
        variants = list(video.get("videos", {}).values())
        choice = _best_variant(variants, params.max_source_dimension, pexels=False)
        if choice is None:
            continue
        ranked.append(
            (
                relevance,
                VideoCandidate(
                    fonte="pixabay",
                    id=str(video["id"]),
                    url=choice["url"],
                    pagina=video.get("pageURL", ""),
                    autor=video.get("user", ""),
                    largura=choice["width"],
                    altura=choice["height"],
                    duracao=seconds,
                ),
            )
        )
    return [candidate for _, candidate in sorted(ranked, key=lambda x: x[0], reverse=True)]


def _download(candidate: VideoCandidate, path: Path, max_bytes: int) -> None:
    """Baixa em streaming e interrompe se o servidor tentar exceder o teto."""
    with requests.get(candidate.url, stream=True, timeout=(10, 60)) as response:
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        if length and int(length) > max_bytes:
            raise ValueError(f"vídeo excede {max_bytes // 1024 // 1024} MB")
        total = 0
        with path.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"vídeo excede {max_bytes // 1024 // 1024} MB")
                output.write(chunk)
    if total == 0:
        raise ValueError("download de B-roll vazio")


def _normalize(source: Path, output: Path, duration: float, params: BrollSourceParams) -> None:
    frames = round(duration * params.fps)
    vf = (
        f"scale={params.width}:{params.height}:force_original_aspect_ratio=increase,"
        f"crop={params.width}:{params.height}:(in_w-out_w)/2:(in_h-out_h)/2,"
        f"fps={params.fps},setsar=1"
    )
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-vf",
        vf,
        "-frames:v",
        str(frames),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg não preparou o B-roll: {result.stderr[-500:]}")
    try:
        meta = probe_clip(output)
    except (ClipProbeError, OSError) as exc:
        raise ValueError("B-roll normalizado não pôde ser lido") from exc
    if (
        (meta.largura, meta.altura) != (params.width, params.height)
        or not math.isclose(meta.fps, params.fps, abs_tol=0.02)
        or meta.tem_audio
        or meta.duracao < (frames - 1) / params.fps
    ):
        raise ValueError("B-roll normalizado não tem resolução, fps ou duração esperados")


def prepare_broll(
    query: str,
    duracao: float,
    *,
    settings: Settings | None = None,
    params: BrollSourceParams | None = None,
) -> PreparedBroll | None:
    """Entrega MP4 pronto do cache ou busca/prepara um; sem resultado retorna ``None``."""
    settings = settings or get_settings()
    params = params or BrollSourceParams(
        width=settings.output_width, height=settings.output_height, fps=settings.output_fps
    )
    query = query.strip()
    if not query:
        return None
    if not math.isfinite(duracao) or not 0 < duracao <= params.max_duration:
        raise ValueError(f"duração do B-roll precisa estar entre 0 e {params.max_duration:g} s")
    key = _key(query, duracao, params)
    output = cache_path("broll_video", key, ".mp4", settings.cache_dir)
    metadata = read_json_cache("broll_video", key, settings.cache_dir)
    if output.is_file() and output.stat().st_size > 0 and metadata is not None:
        try:
            return PreparedBroll.model_validate(metadata).model_copy(update={"arquivo": output})
        except ValueError:
            log.warning("Metadados do B-roll em cache inválidos; refazendo '%s'.", query)

    for search in (_pexels, _pixabay):
        try:
            candidates = search(query, duracao, params, settings)
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            log.warning("Busca de B-roll %s falhou para '%s': %s", search.__name__, query, exc)
            continue
        for candidate in candidates:
            try:
                output.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="broll_", dir=output.parent) as temp:
                    raw = Path(temp) / "origem.mp4"
                    normalized = Path(temp) / "pronto.mp4"
                    _download(candidate, raw, params.max_download_bytes)
                    _normalize(raw, normalized, duracao, params)
                    normalized.replace(output)
                prepared = PreparedBroll(
                    arquivo=output,
                    query=query,
                    duracao=round(round(duracao * params.fps) / params.fps, 6),
                    fonte=candidate.fonte,
                    id=candidate.id,
                    pagina=candidate.pagina,
                    autor=candidate.autor,
                )
                write_json_cache(
                    "broll_video", key, prepared.model_dump(mode="json"), settings.cache_dir
                )
                log.info(
                    "B-roll '%s': %s %s (%dx%d → %dx%d@%d)",
                    query,
                    candidate.fonte,
                    candidate.id,
                    candidate.largura,
                    candidate.altura,
                    params.width,
                    params.height,
                    params.fps,
                )
                return prepared
            except (requests.RequestException, OSError, ValueError, RuntimeError) as exc:
                log.warning(
                    "Vídeo de B-roll %s/%s indisponível: %s",
                    candidate.fonte,
                    candidate.id,
                    exc,
                )
    return None


def prepare_item(
    item: ItemBroll,
    *,
    settings: Settings | None = None,
    params: BrollSourceParams | None = None,
) -> PreparedBroll | None:
    """Entrada direta para um `ItemBroll` validado pelo planejador."""
    if not item.ativo:
        return None
    return prepare_broll(item.query, item.duracao_max, settings=settings, params=params)
