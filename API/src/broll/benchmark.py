"""Mede render, quadros, duração e memória dos presets em um trecho de 4 s.

Uso: uv run python -m src.broll.benchmark --real ../output/samples_palestra_9x16.mp4
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from src.broll.catalog import PRESETS
from src.broll.transitions import Cutaway, apply_cutaways
from src.project import Clip, Timeline
from src.render import _clip_meta, plan_segments


def _run(command: list[str]) -> None:
    subprocess.run(command, capture_output=True, text=True, check=True)


def _peak_bytes(process: subprocess.Popen[str]) -> int:
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong),
                ("peak_working", ctypes.c_size_t), ("working", ctypes.c_size_t),
                ("peak_paged", ctypes.c_size_t), ("paged", ctypes.c_size_t),
                ("peak_nonpaged", ctypes.c_size_t), ("nonpaged", ctypes.c_size_t),
                ("pagefile", ctypes.c_size_t), ("peak_pagefile", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(
            int(process._handle), ctypes.byref(counters), counters.cb
        )
        return int(counters.peak_working) if ok else 0
    status = Path(f"/proc/{process.pid}/status")
    try:
        for line in status.read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def _probe(path: Path) -> dict[str, float | int]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=nb_frames,duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    return {"frames": int(stream["nb_frames"]), "duracao": float(stream["duration"])}


def _audio_hash(path: Path) -> str:
    import hashlib

    decoded = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", str(path),
         "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
        capture_output=True, check=True,
    ).stdout
    return hashlib.sha256(decoded).hexdigest()


def _source(path: Path, *, real: Path | None = None, broll: bool = False) -> None:
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error"]
    if real is not None:
        command += ["-ss", "3", "-t", "4", "-i", str(real), "-vf", "scale=360:640,fps=30"]
    else:
        command += ["-f", "lavfi", "-i",
                    f"{'testsrc' if broll else 'testsrc2'}=s=360x640:r=30:d=4"]
        if not broll:
            command += ["-f", "lavfi", "-i", "sine=f=440:r=48000:d=4"]
    command += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    if broll:
        command += ["-an"]
    else:
        command += ["-c:a", "aac", "-ar", "48000"]
    _run([*command, str(path)])


def benchmark(real: Path | None = None) -> dict:
    """Retorna medições com áudio original e memória de pico do FFmpeg."""
    import src.render as render

    results = {}
    with tempfile.TemporaryDirectory(prefix="benchmark_transicoes_") as folder:
        temp = Path(folder)
        broll = temp / "broll.mp4"
        _source(broll, broll=True)
        for kind, source in (("sintetico", None), ("real", real)):
            if kind == "real" and source is None:
                continue
            camera = temp / f"{kind}.mp4"
            _source(camera, real=source)
            timeline = Timeline(clipes=[Clip(arquivo=str(camera), trechos=[(0, 4)])])
            segments = plan_segments(timeline, [_clip_meta(str(camera), None)], 30)
            original = _probe(camera)
            audio = _audio_hash(camera)
            rows = []
            for preset in PRESETS:
                output = temp / f"{kind}_{preset.id}.mp4"
                peak = 0

                def measure(command: list[str], what: str, cwd: Path | None = None) -> None:
                    nonlocal peak
                    with subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True) as process:
                        while process.poll() is None:
                            peak = max(peak, _peak_bytes(process))
                            time.sleep(0.02)
                        stderr = process.communicate()[1]
                        if process.returncode:
                            raise RuntimeError(f"{what}: {stderr[-800:]}")

                old = render._run_ffmpeg
                render._run_ffmpeg = measure
                started = time.perf_counter()
                try:
                    apply_cutaways(camera, output, [Cutaway(1, 2.5, broll, 0)],
                                   segments, 30, transition=preset.id)
                finally:
                    render._run_ffmpeg = old
                measured = _probe(output)
                rows.append({
                    "preset": preset.id,
                    "latencia_s": round(time.perf_counter() - started, 3),
                    "pico_ffmpeg_mib": round(peak / 2**20, 1),
                    **measured,
                    "audio_igual": _audio_hash(output) == audio,
                    "quadros_iguais": measured["frames"] == original["frames"],
                })
            results[kind] = {"fonte": str(source) if source else "testsrc2 + sine",
                             "original": original, "presets": rows}
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", type=Path, help="vídeo real já disponível")
    parser.add_argument("--output", type=Path, help="destino JSON das medidas")
    args = parser.parse_args()
    result = benchmark(args.real)
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(content)


if __name__ == "__main__":
    main()
