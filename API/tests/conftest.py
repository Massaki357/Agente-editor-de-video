import shutil
import subprocess
from pathlib import Path

import pytest

from src.config import PROJECT_ROOT, get_settings


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path_factory, monkeypatch):
    """Cada teste usa CACHE_DIR e DATA_DIR próprios, nunca os reais."""
    cache_dir = tmp_path_factory.mktemp("cache")
    monkeypatch.setenv("CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("DATA_DIR", str(tmp_path_factory.mktemp("data")))
    # modelo de rosto já baixado: copia para o cache isolado (sem rede nos testes)
    for origem in (
        PROJECT_ROOT / ".cache" / "models" / "blaze_face_short_range.tflite",
        PROJECT_ROOT / ".cache" / "test-assets" / "blaze_face_short_range.tflite",
    ):
        if origem.exists():
            destino = cache_dir / "models" / origem.name
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origem, destino)
            break
    get_settings.cache_clear()
    yield cache_dir
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_real_llm(request, monkeypatch):
    """Nenhum teste chama a API do LLM por acidente (custo, rede, resultado instável).

    Os testes do próprio client mockam a fábrica do modelo; os de integração
    (`-m integration`) podem chamar o LLM de verdade.
    """
    if request.node.path.name == "test_llm_client.py" or request.node.get_closest_marker(
        "integration"
    ):
        return
    from src.llm import client

    def blocked(*args, **kwargs):
        raise client.LLMError("LLM desativado nos testes unitários")

    monkeypatch.setattr(client, "run_structured", blocked)


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
