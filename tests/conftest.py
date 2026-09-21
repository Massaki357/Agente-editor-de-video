import shutil
import subprocess
from pathlib import Path

import pytest

from src.config import get_settings


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path_factory, monkeypatch):
    """Cada teste usa um CACHE_DIR próprio, nunca o `.cache` real."""
    cache_dir = tmp_path_factory.mktemp("cache")
    monkeypatch.setenv("CACHE_DIR", str(cache_dir))
    get_settings.cache_clear()
    yield cache_dir
    get_settings.cache_clear()


requires_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg não está no PATH"
)


def make_video(
    path: Path,
    duration: float = 1.0,
    size: str = "320x180",
    fps: int = 30,
    audio: bool = True,
) -> Path:
    """Gera um vídeo sintético pequeno (testsrc2 + seno de 440 Hz)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    cmd += ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={duration}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}"]
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture(scope="session")
def video_dir(tmp_path_factory) -> Path:
    """Pasta com 1.mp4 (1 s), 2.mp4 (1,5 s) e 10.mp4 (0,5 s, sem áudio), gerada uma vez."""
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("FFmpeg não está no PATH")
    folder = tmp_path_factory.mktemp("videos")
    make_video(folder / "1.mp4", duration=1.0)
    make_video(folder / "2.mp4", duration=1.5)
    make_video(folder / "10.mp4", duration=0.5, audio=False)
    return folder
