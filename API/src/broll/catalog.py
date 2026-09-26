"""Catálogo de transições e prévias sintéticas para escolher efeitos visuais."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path
from typing import Literal

from src.config import REPO_ROOT

Direction = Literal["left", "right", "up", "down"]
Category = Literal["corte", "fade", "movimento", "revelação", "estilizado"]
Cost = Literal["mínimo", "baixo", "médio", "alto"]
DIRECTIONS: tuple[Direction, ...] = ("left", "right", "up", "down")
OPPOSITE = {"left": "right", "right": "left", "up": "down", "down": "up"}


@dataclass(frozen=True)
class Preset:
    id: str
    nome: str
    descricao: str
    categoria: Category
    duracao_padrao: float
    duracao_min: float
    duracao_max: float
    direcoes: tuple[Direction, ...]
    direcao_padrao: Direction | None
    intensidades: tuple[float, ...]
    intensidade_padrao: float
    custo: Cost


PRESETS: tuple[Preset, ...] = (
    Preset("hard_cut", "Corte seco", "Troca imediata, sem mistura de quadros.",
           "corte", 0, 0, 0, (), None, (1.0,), 1.0, "mínimo"),
    Preset("crossfade", "Fusão", "Uma cena desaparece enquanto a outra surge.",
           "fade", 0.25, 0.1, 0.5, (), None, (0.5, 1.0, 1.5), 1.0, "baixo"),
    Preset("slide", "Deslizamento", "A próxima cena desliza para ocupar a tela.",
           "movimento", 0.25, 0.1, 0.5, DIRECTIONS, "left", (1.0,), 1.0, "médio"),
    Preset("wipe", "Varredura", "Uma borda progressiva revela a próxima cena.",
           "revelação", 0.25, 0.1, 0.5, DIRECTIONS, "left", (1.0,), 1.0, "baixo"),
    Preset("reveal", "Revelação", "A cena nova se abre sobre a anterior.",
           "revelação", 0.3, 0.15, 0.5, DIRECTIONS, "left", (1.0,), 1.0, "médio"),
    Preset("zoom", "Zoom de entrada", "A nova cena entra com aproximação.",
           "estilizado", 0.35, 0.2, 0.5, (), None, (1.0,), 1.0, "alto"),
    Preset("blur", "Desfoque", "A troca atravessa um desfoque horizontal.",
           "estilizado", 0.3, 0.15, 0.5, (), None, (1.0,), 1.0, "alto"),
)
BY_ID = {preset.id: preset for preset in PRESETS}


@dataclass(frozen=True)
class Choice:
    preset: Preset
    fps: int
    duracao: float
    frames: int
    direcao: Direction | None
    intensidade: float
    entrada: str | None
    saida: str | None


def effects_for(
    preset: Preset, direction: Direction | None, intensity: float,
) -> tuple[str | None, str | None]:
    """Traduz o preset para nomes conhecidos do xfade, sem expressão arbitrária."""
    if preset.id == "hard_cut":
        return None, None
    if preset.id == "crossfade":
        effect = {0.5: "fadeslow", 1.0: "fade", 1.5: "fadefast"}[intensity]
        return effect, effect
    if preset.id in {"slide", "wipe", "reveal"}:
        assert direction is not None
        return f"{preset.id}{direction}", f"{preset.id}{OPPOSITE[direction]}"
    effect = {"zoom": "zoomin", "blur": "hblur"}[preset.id]
    return effect, effect


def choose(
    preset_id: str, *, fps: int = 30, duration: float | None = None,
    direction: Direction | None = None, intensity: float | None = None,
) -> Choice:
    """Rejeita parâmetros inválidos e fixa a duração na grade de frames."""
    preset = BY_ID.get(preset_id)
    if preset is None:
        raise ValueError(f"preset de transição desconhecido: {preset_id}")
    if type(fps) is not int or fps != 30:
        raise ValueError("catálogo de transições exige 30 fps")
    requested = preset.duracao_padrao if duration is None else duration
    if not isinstance(requested, (int, float)) or not math.isfinite(requested) or (
        not preset.duracao_min <= requested <= preset.duracao_max
    ):
        raise ValueError(
            f"duração de {preset.nome} fora de "
            f"{preset.duracao_min:g}–{preset.duracao_max:g} s"
        )
    frames = min(
        math.floor(preset.duracao_max * fps + 1e-9),
        max(math.ceil(preset.duracao_min * fps - 1e-9), round(requested * fps)),
    )
    if preset.id != "hard_cut" and frames < 1:
        raise ValueError("duração precisa ocupar ao menos um quadro")
    actual_direction = preset.direcao_padrao if direction is None else direction
    if actual_direction not in preset.direcoes and actual_direction is not None:
        raise ValueError(f"direção inválida para {preset.nome}")
    if preset.direcoes and actual_direction is None:
        raise ValueError(f"direção obrigatória para {preset.nome}")
    actual_intensity = preset.intensidade_padrao if intensity is None else intensity
    if not isinstance(actual_intensity, (int, float)) or (
        not math.isfinite(actual_intensity)
        or actual_intensity not in preset.intensidades
    ):
        raise ValueError(f"intensidade inválida para {preset.nome}")
    entry, exit_effect = effects_for(preset, actual_direction, actual_intensity)
    return Choice(preset, fps, frames / fps, frames, actual_direction, actual_intensity,
                  entry, exit_effect)


def validate_placement(
    choice: Choice, *, fps: int, start: float, end: float,
    clip_start: float, clip_end: float, previous_end: float | None = None,
    next_start: float | None = None,
) -> None:
    """Exige retorno à câmera, margem para os fades e ausência de emendas."""
    values = (start, end, clip_start, clip_end, previous_end, next_start)
    if fps != choice.fps or any(value is not None and not math.isfinite(value) for value in values):
        raise ValueError("tempos ou fps inválidos")
    a, b, c, d = (round(value * fps) for value in (start, end, clip_start, clip_end))
    if not c <= a < b < d:
        raise ValueError("cutaway fora do clipe ou sem retorno à câmera")
    f = choice.frames
    if f and (a - c < f or d - b < f or b - a <= 2 * f):
        raise ValueError("trecho curto demais para entrada e saída da transição")
    if previous_end is not None and a - round(previous_end * fps) < 2 * f + 1:
        raise ValueError("intervalo curto demais desde o cutaway anterior")
    if next_start is not None and round(next_start * fps) - b < 2 * f + 1:
        raise ValueError("intervalo curto demais até o próximo cutaway")


def installed_xfade_effects() -> set[str]:
    """Inventaria o filtro xfade do FFmpeg disponível nesta máquina."""
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-h", "filter=xfade"],
        capture_output=True, text=True, check=True,
    )
    text = completed.stdout + completed.stderr
    return set(re.findall(r"^\s+([a-z][a-z0-9]*)\s+-?\d+\s+\.\.FV", text, re.M))


def render_preview(choice: Choice, output: Path, *, fps: int = 30,
                   width: int = 180, height: int = 320) -> float:
    """Demonstra câmera → B-roll → câmera em 4,5 s, sem usar mídias do usuário."""
    if fps != choice.fps or width <= 0 or height <= 0 or width % 2 or height % 2:
        raise ValueError("prévia exige fps e dimensões pares positivos")
    validate_placement(choice, fps=fps, start=1.5, end=3.0, clip_start=0, clip_end=4.5)
    effects = installed_xfade_effects()
    if any(effect not in effects for effect in (choice.entrada, choice.saida) if effect):
        raise ValueError(f"xfade indisponível para {choice.preset.nome}")
    f = choice.frames
    base_frames = 45
    first_frames, second_frames, last_frames = base_frames + f, base_frames + f, base_frames
    sources = (
        ("testsrc2", first_frames),
        ("testsrc", second_frames),
        ("testsrc2", last_frames),
    )
    command = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    for source, frames in sources:
        command += ["-f", "lavfi", "-i", (
            f"{source}=size={width}x{height}:rate={fps}:duration={frames / fps:.6f}"
        )]
    command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4.5"]
    graph = [
        f"[{i}:v]fps={fps},trim=end_frame={frames},setpts=PTS-STARTPTS,"
        f"settb=AVTB,format=yuv420p[p{i}]"
        for i, (_, frames) in enumerate(sources)
    ]
    if choice.preset.id == "hard_cut":
        graph.append("[p0][p1][p2]concat=n=3:v=1:a=0[v]")
    else:
        graph += [
            f"[p0][p1]xfade=transition={choice.entrada}:duration={f / fps:.6f}:"
            f"offset={base_frames / fps:.6f}[x]",
            f"[x][p2]xfade=transition={choice.saida}:duration={f / fps:.6f}:"
            f"offset={2 * base_frames / fps:.6f}[v]",
        ]
    output.parent.mkdir(parents=True, exist_ok=True)
    command += ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "3:a:0",
                "-frames:v", str(3 * base_frames), "-c:v", "libx264",
                "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-b:a", "96k", "-shortest", str(output)]
    start_time = time.perf_counter()
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        output.unlink(missing_ok=True)
        raise ValueError(f"falha na prévia {choice.preset.nome}: {exc.stderr[-600:]}") from exc
    return time.perf_counter() - start_time


def generate_catalog(output_dir: Path, *, fps: int = 30) -> Path:
    """Gera MP4 por preset, inventário, medidas locais e índice HTML."""
    output_dir.mkdir(parents=True, exist_ok=True)
    available = installed_xfade_effects()
    entries = []
    for preset in PRESETS:
        choice = choose(preset.id, fps=fps)
        file = f"{preset.id}.mp4"
        seconds = render_preview(choice, output_dir / file, fps=fps)
        entries.append({
            **asdict(preset), "arquivo": file, "duracao_efetiva": choice.duracao,
            "xfade_entrada": choice.entrada, "xfade_saida": choice.saida,
            "render_segundos": round(seconds, 3),
            "custo_ms_por_quadro": round(seconds * 1000 / (round(4.5 * fps)), 2),
        })
    manifest = output_dir / "catalogo.json"
    manifest.write_text(json.dumps({
        "fps": fps, "duracao_previa": 4.5, "efeitos_ffmpeg": sorted(available),
        "presets": entries,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    cards = "\n".join(
        f'<article><h2>{escape(entry["nome"])}</h2>'
        f'<video controls playsinline src="{entry["arquivo"]}"></video>'
        f'<p>{escape(entry["descricao"])} Custo estimado: {entry["custo"]} '
        f'({entry["custo_ms_por_quadro"]} ms/quadro nesta prévia).</p></article>'
        for entry in entries
    )
    (output_dir / "index.html").write_text(
        '<!doctype html><html lang="pt-br"><meta charset="utf-8">'
        '<title>Transições de B-roll</title><style>body{font:16px sans-serif;'
        'background:#151923;color:#f4f4f4;padding:24px}main{display:grid;'
        'grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:18px}'
        'article{background:#252b39;padding:14px;border-radius:10px}'
        'video{width:100%;max-width:240px}</style><h1>Transições de B-roll</h1>'
        '<p>Câmera → B-roll → câmera. Mesma cena e áudio sintéticos.</p>'
        f'<main>{cards}</main></html>',
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera prévias do catálogo de transições")
    parser.add_argument("output_dir", nargs="?", type=Path,
                        default=REPO_ROOT / "output" / "parte6_etapa0_catalogo")
    args = parser.parse_args()
    print(generate_catalog(args.output_dir))


if __name__ == "__main__":
    main()
