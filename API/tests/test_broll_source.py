"""Parte 3, Etapa 1: provedores, limite de download, normalização e cache."""

import shutil

import pytest

import src.broll.source as source
from src.broll.planner import ItemBroll
from src.broll.source import BrollSourceParams, VideoCandidate, prepare_broll, prepare_item
from src.clips import probe_clip
from src.config import Settings

from .conftest import make_video, requires_ffmpeg


class Response:
    def __init__(self, payload=None, chunks=(), headers=None):
        self.payload = payload or {}
        self.chunks = chunks
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        return iter(self.chunks)


def candidate(provider="pexels", url="https://example.com/video.mp4"):
    return VideoCandidate(
        fonte=provider,
        id="123",
        url=url,
        pagina="https://example.com/page",
        autor="Autor",
        largura=1280,
        altura=720,
        duracao=5,
    )


def test_pexels_uses_video_endpoint_and_largest_mp4_below_ceiling(monkeypatch):
    calls = []
    payload = {
        "videos": [
            {
                "id": 10,
                "url": "https://www.pexels.com/video/coffee-harvest-10/",
                "duration": 6,
                "user": {"name": "Ana"},
                "video_files": [
                    {
                        "link": "https://cdn/a.mp4",
                        "file_type": "video/mp4",
                        "width": 640,
                        "height": 360,
                    },
                    {
                        "link": "https://cdn/b.mp4",
                        "file_type": "video/mp4",
                        "width": 1920,
                        "height": 1080,
                    },
                    {
                        "link": "https://cdn/4k.mp4",
                        "file_type": "video/mp4",
                        "width": 3840,
                        "height": 2160,
                    },
                    {
                        "link": "https://cdn/stream.m3u8",
                        "file_type": "video/mp4",
                        "width": None,
                        "height": None,
                    },
                ],
            },
            {"id": 11, "duration": 1, "video_files": []},
        ]
    }

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response(payload)

    monkeypatch.setattr(source.requests, "get", get)
    results = source._pexels(
        "coffee harvest", 3.1, BrollSourceParams(), Settings(pexels_api_key="fake")
    )
    assert len(results) == 1
    assert results[0].url == "https://cdn/b.mp4" and results[0].autor == "Ana"
    assert calls[0][0] == "https://api.pexels.com/v1/videos/search"
    assert calls[0][1]["params"]["query"] == "coffee harvest"
    assert calls[0][1]["headers"]["Authorization"] == "fake"


def test_pixabay_uses_video_endpoint_and_caches_search_for_24_hours(tmp_path, monkeypatch):
    calls = []
    payload = {
        "hits": [
            {
                "id": 20,
                "pageURL": "https://pixabay.com/videos/id-20/",
                "user": "Beto",
                "duration": 8,
                "tags": "coffee, harvest",
                "videos": {
                    "large": {"url": "https://cdn/4k.mp4", "width": 3840, "height": 2160},
                    "medium": {"url": "https://cdn/hd.mp4", "width": 1920, "height": 1080},
                    "small": {"url": "https://cdn/small.mp4", "width": 640, "height": 360},
                },
            }
        ]
    }

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response(payload)

    monkeypatch.setattr(source.requests, "get", get)
    monkeypatch.setattr(source.time, "time", lambda: 1000.0)
    settings = Settings(cache_dir=tmp_path / "cache", pixabay_api_key="fake")
    first = source._pixabay("coffee", 3, BrollSourceParams(), settings)
    second = source._pixabay("coffee", 3, BrollSourceParams(), settings)
    assert first == second and first[0].url == "https://cdn/hd.mp4"
    assert calls == [("https://pixabay.com/api/videos/", calls[0][1])]
    assert calls[0][1]["params"]["video_type"] == "film"
    monkeypatch.setattr(source.time, "time", lambda: 1000.0 + 24 * 60 * 60 + 1)
    source._pixabay("coffee", 3, BrollSourceParams(), settings)
    assert len(calls) == 2


def test_search_discards_unrelated_videos_and_ranks_relevant_titles(monkeypatch):
    def video(id, slug):
        return {
            "id": id,
            "url": f"https://www.pexels.com/video/{slug}-{id}/",
            "duration": 8,
            "video_files": [
                {
                    "link": f"https://cdn/{id}.mp4",
                    "file_type": "video/mp4",
                    "width": 1080,
                    "height": 1920,
                }
            ],
        }

    payload = {
        "videos": [
            video(1, "shaking-basket-with-fruit"),
            video(2, "workers-picking-coffee-beans"),
            video(3, "coffee-brewing"),
        ]
    }
    monkeypatch.setattr(source.requests, "get", lambda *a, **k: Response(payload))
    result = source._pexels(
        "workers harvesting coffee",
        3,
        BrollSourceParams(),
        Settings(pexels_api_key="fake"),
    )
    assert [item.id for item in result] == ["2"]
    assert source._relevance("coffee harvest", "fruit basket") == 0
    assert not source._is_relevant("workers harvesting coffee", "coffee brewing")


def test_pixabay_ignores_unrelated_tags(tmp_path, monkeypatch):
    payload = {
        "hits": [
            {
                "id": 1,
                "tags": "basket, fruit",
                "duration": 5,
                "videos": {
                    "medium": {"url": "https://cdn/fruit.mp4", "width": 1080, "height": 1920}
                },
            },
        ]
    }
    monkeypatch.setattr(source.requests, "get", lambda *a, **k: Response(payload))
    assert (
        source._pixabay(
            "coffee harvest",
            2,
            BrollSourceParams(),
            Settings(cache_dir=tmp_path / "cache", pixabay_api_key="fake"),
        )
        == []
    )


def test_download_stream_has_a_hard_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        source.requests,
        "get",
        lambda *args, **kwargs: Response(chunks=[b"1234", b"5678"]),
    )
    with pytest.raises(ValueError, match="excede"):
        source._download(candidate(), tmp_path / "source.mp4", max_bytes=5)
    assert (tmp_path / "source.mp4").stat().st_size <= 5


@requires_ffmpeg
def test_pixabay_fallback_normalizes_and_reuses_a_clip(tmp_path, monkeypatch):
    original = make_video(tmp_path / "original.mp4", duration=2.0, size="320x180")
    calls = []
    monkeypatch.setattr(source, "_pexels", lambda *a: calls.append("pexels") or [])
    monkeypatch.setattr(
        source, "_pixabay", lambda *a: calls.append("pixabay") or [candidate("pixabay")]
    )

    def download(clip, path, limit):
        calls.append("download")
        shutil.copyfile(original, path)

    monkeypatch.setattr(source, "_download", download)
    settings = Settings(cache_dir=tmp_path / "cache")
    params = BrollSourceParams(width=270, height=480)
    first = prepare_broll("coffee harvest", 1.5, settings=settings, params=params)
    assert first is not None and first.arquivo.is_file()
    assert first.fonte == "pixabay" and first.pagina == "https://example.com/page"
    meta = probe_clip(first.arquivo)
    assert (meta.largura, meta.altura) == (270, 480)
    assert meta.fps == pytest.approx(30, abs=0.01)
    assert meta.duracao == pytest.approx(1.5, abs=1 / 30)
    assert not meta.tem_audio  # no futuro, a voz do vídeo original continua
    mtime = first.arquivo.stat().st_mtime_ns
    second = prepare_broll(" COFFEE HARVEST ", 1.5, settings=settings, params=params)
    assert second is not None and second.arquivo == first.arquivo
    assert second.arquivo.stat().st_mtime_ns == mtime
    assert calls == ["pexels", "pixabay", "download"]
    third = prepare_broll("coffee harvest", 1.6, settings=settings, params=params)
    assert third is not None and third.arquivo != first.arquivo
    assert calls == ["pexels", "pixabay", "download"] * 2


@requires_ffmpeg
def test_bad_pexels_file_falls_back_to_pixabay(tmp_path, monkeypatch):
    original = make_video(tmp_path / "original.mp4", duration=2.0)
    seen = []
    monkeypatch.setattr(source, "_pexels", lambda *a: [candidate("pexels")])
    monkeypatch.setattr(source, "_pixabay", lambda *a: [candidate("pixabay")])

    def download(clip, path, limit):
        seen.append(clip.fonte)
        if clip.fonte == "pexels":
            raise ValueError("arquivo inválido")
        shutil.copyfile(original, path)

    monkeypatch.setattr(source, "_download", download)
    result = prepare_broll(
        "coffee",
        1.5,
        settings=Settings(cache_dir=tmp_path / "cache"),
        params=BrollSourceParams(width=270, height=480),
    )
    assert result is not None and result.fonte == "pixabay"
    assert seen == ["pexels", "pixabay"]


@requires_ffmpeg
def test_default_output_is_1080x1920_at_30fps_without_audio(tmp_path):
    original = make_video(tmp_path / "portrait.mp4", duration=1.6, size="540x960")
    output = tmp_path / "normalized.mp4"
    source._normalize(original, output, 1.5, BrollSourceParams())
    meta = probe_clip(output)
    assert (meta.largura, meta.altura, meta.fps) == (1080, 1920, 30)
    assert not meta.tem_audio


def test_missing_results_or_inactive_item_return_none(tmp_path):
    settings = Settings(cache_dir=tmp_path / "cache")
    assert prepare_broll("coffee", 2, settings=settings) is None
    assert prepare_broll("   ", 2, settings=settings) is None
    with pytest.raises(ValueError, match="duração"):
        prepare_broll("coffee", 5, settings=settings)
    item = ItemBroll(
        id=0,
        clipe=0,
        trecho_inicio_palavra=0,
        trecho_fim_palavra=4,
        texto="café",
        query="coffee",
        inicio=0,
        duracao_max=2,
        motivo="visual",
        ativo=False,
    )
    assert prepare_item(item, settings=settings) is None
