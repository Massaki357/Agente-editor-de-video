"""Parte 2, Etapa 2: CLI, progresso e erros úteis."""

import io
import subprocess
import sys

import pytest

from src.video import cli, stabilize

from .conftest import make_video, requires_ffmpeg


def test_cli_reports_missing_file(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "setup_logging", lambda **kwargs: None)
    assert cli.main([str(tmp_path / "ausente.mp4"), str(tmp_path / "saida.mp4")]) == 1
    assert "não encontrado" in capsys.readouterr().err


def test_cli_reports_missing_vidstab_and_invalid_input(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "setup_logging", lambda **kwargs: None)
    entrada = tmp_path / "invalido.mp4"
    entrada.write_bytes(b"nao e um video")
    saida = tmp_path / "saida.mp4"
    monkeypatch.setattr(stabilize, "_ffmpeg_filters", lambda: {"vidstabdetect"})
    assert cli.main([str(entrada), str(saida), "--metodo", "vidstab"]) == 1
    assert "vidstabtransform" in capsys.readouterr().err
    monkeypatch.setattr(stabilize, "_ffmpeg_filters", lambda: set(stabilize.VIDSTAB_FILTERS))
    assert cli.main([str(entrada), str(saida)]) == 1
    assert "vídeo de entrada inválido" in capsys.readouterr().err
    assert not saida.exists()


@requires_ffmpeg
def test_cli_rejects_audio_only_input(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "setup_logging", lambda **kwargs: None)
    entrada = tmp_path / "audio.m4a"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            str(entrada),
        ],
        check=True,
        capture_output=True,
    )
    assert cli.main([str(entrada), str(tmp_path / "saida.mp4")]) == 1
    assert "vídeo de entrada inválido" in capsys.readouterr().err


def test_bar_prints_one_line_per_pass_when_redirected(tmp_path):
    out = tmp_path / "progresso.txt"
    with out.open("w", encoding="utf-8") as fluxo:
        barra = cli.Barra(fluxo)
        for etapa in ("análise", "estabilização"):
            for fracao in (0, 0.1, 0.5, 1):
                barra(etapa, fracao)
    linhas = out.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 2
    assert "1/2" in linhas[0] and "2/2" in linhas[1]


def test_bar_updates_percentages_in_a_terminal():
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    terminal = Terminal()
    barra = cli.Barra(terminal)
    barra("análise", 0)
    barra("análise", 0.5)
    barra("análise", 1)
    texto = terminal.getvalue()
    assert "\r" in texto and "50%" in texto and "100%" in texto


@requires_ffmpeg
def test_module_cli_runs_both_passes_and_reports_progress(tmp_path):
    if not set(stabilize.VIDSTAB_FILTERS) <= (stabilize._ffmpeg_filters() or set()):
        pytest.skip("FFmpeg sem libvidstab")
    entrada = make_video(tmp_path / "entrada.mp4", duration=1.5)
    saida = tmp_path / "saida.mp4"
    cmd = [
        sys.executable,
        "-m",
        "src.video.stabilize",
        str(entrada),
        str(saida),
        "--smoothing",
        "forte",
        "--crop",
        "5",
        "--sem-cache",
    ]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90
    )
    assert proc.returncode == 0, proc.stderr
    assert saida.is_file() and saida.stat().st_size > 0
    assert "Passada 1/2" in proc.stderr
    assert "Passada 2/2" in proc.stderr
    assert "Vídeo estabilizado:" in proc.stdout


@requires_ffmpeg
def test_core_progress_covers_two_passes_and_cache(tmp_path):
    if not set(stabilize.VIDSTAB_FILTERS) <= (stabilize._ffmpeg_filters() or set()):
        pytest.skip("FFmpeg sem libvidstab")
    entrada = make_video(tmp_path / "entrada.mp4", duration=2)
    eventos = []
    estabilizado = tmp_path / "saida.mp4"
    stabilize.stabilize_video(
        entrada, estabilizado, on_progress=lambda e, f: eventos.append((e, f))
    )
    for etapa in ("análise", "estabilização"):
        fracoes = [f for e, f in eventos if e == etapa]
        assert fracoes[0] == 0.0 and fracoes[-1] == 1.0
        assert all(0 <= f <= 1 for f in fracoes)
    eventos.clear()
    stabilize.stabilize_video(
        entrada, tmp_path / "saida_cache.mp4", on_progress=lambda e, f: eventos.append((e, f))
    )
    assert eventos == [("cache", 1.0)]


@pytest.mark.integration
@requires_ffmpeg
def test_cli_completes_a_two_and_half_minute_video(tmp_path):
    """Vídeo longo fica fora da suíte rápida, mas cobre travas nas duas passadas."""
    if not set(stabilize.VIDSTAB_FILTERS) <= (stabilize._ffmpeg_filters() or set()):
        pytest.skip("FFmpeg sem libvidstab")
    entrada = make_video(tmp_path / "longo.mp4", duration=150, size="320x180")
    saida = tmp_path / "longo_estabilizado.mp4"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.video.stabilize",
            str(entrada),
            str(saida),
            "--smoothing",
            "medio",
            "--sem-cache",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Passada 1/2" in proc.stderr and "Passada 2/2" in proc.stderr
    duracao = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(saida),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert float(duracao) == pytest.approx(150, abs=0.1)
