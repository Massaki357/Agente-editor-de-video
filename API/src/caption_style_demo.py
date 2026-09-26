"""Gera um vídeo comparativo dos presets visuais de legenda contínua.

Uso, dentro de ``API/``::

    uv run python -m src.caption_style_demo
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from src.caption_presets import resolve_preset
from src.captions import write_captions
from src.clips import project_from_files
from src.config import REPO_ROOT
from src.render import render_timeline
from src.transcribe import Palavra

DEMO_PRESETS = ("clean", "bold", "minimal", "cinematic", "social", "karaoke")
DEMO_TEXT = ("A edição certa transforma ideias em histórias memoráveis.").split()


def _run(command: list[str], label: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"FFmpeg falhou em {label}: {result.stderr[-800:]}")


def _base_video(path: Path, size: tuple[int, int], duration: float, fps: int) -> None:
    width, height = size
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate={fps}:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=330:sample_rate=48000:duration={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        "fonte sintética",
    )


def _words(duration: float) -> list[Palavra]:
    start = 0.3
    step = (duration - 0.6) / len(DEMO_TEXT)
    return [
        Palavra(
            indice=index, texto=text, inicio=start + index * step, fim=start + (index + 0.82) * step
        )
        for index, text in enumerate(DEMO_TEXT)
    ]


def generate_demo(
    output: Path,
    *,
    size: tuple[int, int] = (360, 640),
    fps: int = 30,
    duration_per_preset: float = 3.0,
) -> Path:
    """Renderiza a mesma cena/texto com os seis estilos e cria um mapa JSON."""
    if fps != 30 or min(size) < 2 or any(value % 2 for value in size):
        raise ValueError("a demonstração exige 30 fps e dimensões pares positivas")
    if duration_per_preset < 2:
        raise ValueError("cada preset precisa de ao menos 2 segundos")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="caption_styles_", dir=output.parent) as folder:
        work = Path(folder)
        source = work / "source.mp4"
        _base_video(source, size, duration_per_preset, fps)
        project = project_from_files([source])
        words = _words(duration_per_preset)
        rendered: list[Path] = []
        entries = []
        for index, preset_name in enumerate(DEMO_PRESETS):
            preset = resolve_preset(preset_name)
            ass = write_captions(
                [(words, (0.0, duration_per_preset))],
                work / f"{preset_name}.ass",
                preset.style.for_output(size),
                size,
            )
            video = work / f"{index:02d}_{preset_name}.mp4"
            render_timeline(project.timeline, video, size=size, legendas=ass)
            rendered.append(video)
            entries.append(
                {
                    "preset": preset_name,
                    "descricao": preset.descricao,
                    "inicio": round(index * duration_per_preset, 3),
                    "fim": round((index + 1) * duration_per_preset, 3),
                }
            )
        concat = work / "concat.txt"
        concat.write_text(
            "".join(f"file '{path.as_posix()}'\n" for path in rendered), encoding="utf-8"
        )
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-c",
                "copy",
                str(output),
            ],
            "concatenação da demonstração",
        )
    output.with_suffix(".json").write_text(
        json.dumps(
            {
                "texto": " ".join(DEMO_TEXT),
                "fps": fps,
                "saida": list(size),
                "duracao_por_preset": duration_per_preset,
                "presets": entries,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera comparação dos estilos de legenda")
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=REPO_ROOT / "output" / "caption_styles_etapa3.mp4",
    )
    args = parser.parse_args()
    print(generate_demo(args.output))


if __name__ == "__main__":
    main()
