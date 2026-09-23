"""Parte 1, Etapa 2: CLI do otimizador (`python -m src.audio.optimize`)."""

import io
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from src.audio import cli
from src.audio.metrics import Medidas
from src.audio.optimize import ResultadoAudio

from .conftest import requires_ffmpeg


def wav_teste(path: Path, dur: float = 2.0) -> Path:
    taxa = 48_000
    t = np.arange(int(taxa * dur)) / taxa
    rng = np.random.default_rng(2)
    fala = (np.sin(2 * np.pi * 2 * t) > 0).astype(float) * np.sin(2 * np.pi * 220 * t)
    dados = np.clip(0.2 * fala + rng.normal(0, 10 ** (-35 / 20), len(t)), -1, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes((dados * 32767).astype(np.int16).tobytes())
    return path


def _probe(path: Path) -> dict:
    saida = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(saida)["streams"][0]


# ------------------------------------------------------------------ formato da saída


@pytest.mark.parametrize(
    ("nome", "pedido", "esperado"),
    [
        ("saida.wav", None, "wav"),
        ("saida.mp3", None, "mp3"),
        ("saida.MP3", None, "mp3"),
        ("saida.wav", "mp3", "mp3"),  # --format manda
        ("saida", None, "wav"),
    ],
)
def test_output_format_comes_from_the_extension_or_the_flag(nome, pedido, esperado):
    assert cli.formato_de(Path(nome), pedido) == esperado


@pytest.mark.parametrize("ext", [".ogg", ".m4a", ".flac", ".aac"])
def test_unsupported_output_extension_is_refused(ext):
    """Gravar WAV com nome .ogg deixaria o usuário com um arquivo que players recusam."""
    with pytest.raises(cli.FormatoNaoSuportado, match="use .wav ou .mp3"):
        cli.formato_de(Path("saida" + ext), None)


@requires_ffmpeg
def test_unsupported_extension_exits_with_code_2(tmp_path, capsys):
    entrada = wav_teste(tmp_path / "ruido.wav")
    assert cli.main([str(entrada), str(tmp_path / "saida.ogg"), "-q"]) == 2
    assert "não sei gravar .ogg" in capsys.readouterr().err
    assert not (tmp_path / "saida.ogg").exists()


# ------------------------------------------------------------------ barra de progresso


def test_progress_bar_redraws_a_single_line_on_a_terminal():
    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    saida = Terminal()
    barra = cli.Barra(ativa=True, saida=saida)
    barra("reduzindo o ruído", 0.35)
    barra("pronto", 1.0)
    barra.fim()
    texto = saida.getvalue()
    assert texto.count("\r") == 2  # reescreve a mesma linha
    assert "35%" in texto and "100%" in texto and "reduzindo o ruído" in texto
    assert "#" * 28 in texto  # a barra cheia no fim


def test_progress_prints_one_line_per_step_when_redirected():
    saida = io.StringIO()  # sem isatty: saída redirecionada
    barra = cli.Barra(ativa=True, saida=saida)
    barra("medindo", 0.1)
    barra("medindo", 0.14)  # mesma etapa: não repete
    barra("pronto", 1.0)
    linhas = [linha for linha in saida.getvalue().splitlines() if linha.strip()]
    assert len(linhas) == 2 and "\r" not in saida.getvalue()


def test_quiet_mode_prints_nothing():
    saida = io.StringIO()
    barra = cli.Barra(ativa=False, saida=saida)
    barra("medindo", 0.5)
    barra.fim()
    assert saida.getvalue() == ""


# ------------------------------------------------------------------ resumo


def test_summary_shows_before_and_after():
    r = ResultadoAudio(
        caminho=Path("x.wav"),
        motor="deepfilternet",
        antes=Medidas(-20.0, -40.0, -3.0),
        depois=Medidas(-16.0, -70.0, -1.5),
        lufs_antes=-28.0,
        lufs_depois=-16.0,
    )
    texto = cli.resumo(r, Path("saida.wav"))
    assert "antes" in texto and "depois" in texto
    assert "-28.0" in texto and "-16.0" in texto  # volume antes e depois
    assert "SNR +34.0 dB" in texto and "deepfilternet" in texto
    assert "do cache" not in texto
    assert "do cache" in cli.resumo(
        ResultadoAudio(Path("x"), "afftdn", r.antes, r.depois, -28.0, -16.0, do_cache=True),
        Path("s.wav"),
    )


# ------------------------------------------------------------------ ponta a ponta


@requires_ffmpeg
def test_cli_cleans_a_wav(tmp_path, capsys):
    entrada = wav_teste(tmp_path / "ruido.wav")
    saida = tmp_path / "limpo.wav"
    codigo = cli.main([str(entrada), str(saida), "--motor", "afftdn", "-q"])
    assert codigo == 0 and saida.is_file()
    texto = capsys.readouterr().out
    assert "SNR +" in texto and "afftdn" in texto
    assert _probe(saida)["codec_name"] == "pcm_s16le"


@requires_ffmpeg
def test_cli_accepts_a_video_and_writes_mp3(tmp_path, video_dir, capsys):
    saida = tmp_path / "limpo.mp3"
    codigo = cli.main([str(video_dir / "1.mp4"), str(saida), "--motor", "afftdn", "-q"])
    assert codigo == 0 and saida.is_file()
    assert _probe(saida)["codec_name"] == "mp3"
    assert "SNR +" in capsys.readouterr().out


@requires_ffmpeg
def test_format_flag_wins_over_the_extension(tmp_path, video_dir):
    saida = tmp_path / "parece_wav.wav"
    assert cli.main([str(video_dir / "1.mp4"), str(saida), "--format", "mp3", "-q"]) == 0
    assert _probe(saida)["codec_name"] == "mp3"


@requires_ffmpeg
def test_cli_reports_the_cache_on_the_second_run(tmp_path, capsys):
    entrada = wav_teste(tmp_path / "ruido.wav")
    args = [str(entrada), str(tmp_path / "a.wav"), "--motor", "afftdn", "-q"]
    assert cli.main(args) == 0
    capsys.readouterr()
    assert cli.main([*args[:1], str(tmp_path / "b.wav"), *args[2:]]) == 0
    assert "do cache" in capsys.readouterr().out


@requires_ffmpeg
def test_cli_accepts_an_mp3_input(tmp_path, capsys):
    """O critério de aceite cita o .mp3 explicitamente."""
    wav = wav_teste(tmp_path / "fonte.wav")
    mp3 = tmp_path / "fonte.mp3"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
        + ["-i", str(wav), "-c:a", "libmp3lame", "-q:a", "4", str(mp3)],
        check=True,
        capture_output=True,
    )
    saida = tmp_path / "limpo.mp3"
    assert cli.main([str(mp3), str(saida), "--motor", "afftdn", "-q"]) == 0
    assert _probe(saida)["codec_name"] == "mp3"
    assert "SNR +" in capsys.readouterr().out


@requires_ffmpeg
def test_a_file_without_audio_says_so(tmp_path, video_dir, capsys):
    """`10.mp4` do conftest não tem trilha de áudio."""
    assert cli.main([str(video_dir / "10.mp4"), str(tmp_path / "s.wav"), "-q"]) == 1
    erro = capsys.readouterr().err
    assert "não tem trilha de áudio" in erro and "10.mp4" in erro


@requires_ffmpeg
def test_a_failed_conversion_keeps_the_previous_file(tmp_path, monkeypatch):
    """Erro no meio da conversão não pode destruir o arquivo que já estava lá."""
    wav = wav_teste(tmp_path / "limpo.wav")
    destino = tmp_path / "importante.mp3"
    destino.write_bytes(b"conteudo antigo")

    def ffmpeg_falha(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "Error: disco cheio")

    monkeypatch.setattr(cli.subprocess, "run", ffmpeg_falha)
    with pytest.raises(RuntimeError, match="falhou ao gravar"):
        cli.converter(wav, destino, "mp3")
    assert destino.read_bytes() == b"conteudo antigo"


def test_progress_bar_survives_a_broken_stream():
    """stderr fechado (ou ausente) não pode virar traceback."""

    class Quebrado:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    barra = cli.Barra(ativa=True, saida=Quebrado())
    barra("medindo", 0.5)  # não levanta
    barra.fim()


def test_missing_file_exits_with_a_clear_message(tmp_path, capsys):
    codigo = cli.main([str(tmp_path / "nao_existe.mp3"), str(tmp_path / "s.wav"), "-q"])
    assert codigo == 1
    assert "não encontrado" in capsys.readouterr().err


@requires_ffmpeg
def test_a_file_that_is_not_audio_explains_itself(tmp_path, capsys):
    ruim = tmp_path / "texto.mp3"
    ruim.write_text("isto não é áudio", encoding="utf-8")
    codigo = cli.main([str(ruim), str(tmp_path / "s.wav"), "-q"])
    assert codigo == 1
    erro = capsys.readouterr().err
    assert "não parece um vídeo válido" in erro and "texto.mp3" in erro


def test_invalid_parameter_is_rejected_before_processing(tmp_path, capsys):
    entrada = wav_teste(tmp_path / "ruido.wav")
    codigo = cli.main([str(entrada), str(tmp_path / "s.wav"), "--aggressiveness", "5", "-q"])
    assert codigo == 2
    assert "parâmetro inválido" in capsys.readouterr().err


@requires_ffmpeg
def test_cancelling_with_ctrl_c_is_not_a_crash(tmp_path, monkeypatch, capsys):
    entrada = wav_teste(tmp_path / "ruido.wav")

    def interrompe(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "optimize_audio", interrompe)
    assert cli.main([str(entrada), str(tmp_path / "s.wav"), "-q"]) == 130
    assert "cancelado" in capsys.readouterr().err


@requires_ffmpeg
def test_module_entry_point_runs(tmp_path):
    """`python -m src.audio.optimize` é o comando da etapa."""
    entrada = wav_teste(tmp_path / "ruido.wav")
    proc = subprocess.run(
        # o mesmo interpretador do pytest: `python` do PATH não tem as dependências
        [sys.executable, "-m", "src.audio.optimize", str(entrada), str(tmp_path / "s.wav")]
        + ["--motor", "afftdn", "-q"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert proc.returncode == 0, proc.stderr
    assert "SNR +" in proc.stdout and (tmp_path / "s.wav").is_file()
