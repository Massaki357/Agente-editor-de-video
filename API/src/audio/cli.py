"""Otimizador de áudio na linha de comando (Parte 1, Etapa 2).

    python -m src.audio.optimize aula.mp4 limpo.wav
    python -m src.audio.optimize podcast.mp3 limpo.mp3 --aggressiveness 0.8
    python -m src.audio.optimize entrevista.wav saida.mp3 --format mp3 --sem-normalizar

Aceita vídeo ou áudio na entrada (o áudio é extraído com o FFmpeg). O formato da saída
vem da extensão do arquivo, ou de `--format`. **A saída é sempre mono, 48 kHz** — é o que
a transcrição e os motores de limpeza usam. No fim mostra o antes e o depois: nível da
fala, piso de ruído, SNR e volume em LUFS.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from src.audio.metrics import Medidas
from src.audio.optimize import AudioParams, ResultadoAudio, optimize_audio
from src.errors import mensagem_amigavel
from src.logging_setup import setup_logging

log = logging.getLogger(__name__)

FORMATOS = ("wav", "mp3")
LARGURA_BARRA = 28


class Barra:
    """Barra de progresso simples: `[####----] 45%  reduzindo o ruído  12s`.

    Fora de um terminal (saída redirecionada), imprime uma linha por etapa em vez de
    reescrever a mesma linha com `\\r`.
    """

    def __init__(self, ativa: bool = True, saida=None):
        self.saida = saida or sys.stderr
        # terminal estranho (stderr fechado, pythonw sem stderr): sem barra, sem traceback
        try:
            terminal = bool(self.saida) and self.saida.isatty()
        except (AttributeError, ValueError, OSError):
            terminal = False
            self.saida = None
        self.ativa = ativa and terminal
        self.silenciosa = not ativa
        self.inicio = time.perf_counter()
        self.ultima = ""

    def __call__(self, etapa: str, fracao: float) -> None:
        if self.silenciosa or self.saida is None:
            return
        decorrido = time.perf_counter() - self.inicio
        if not self.ativa:  # log simples quando não é terminal
            if etapa != self.ultima:
                print(f"{etapa}… ({decorrido:.0f}s)", file=self.saida, flush=True)
                self.ultima = etapa
            return
        cheio = int(round(LARGURA_BARRA * min(max(fracao, 0.0), 1.0)))
        barra = "#" * cheio + "-" * (LARGURA_BARRA - cheio)
        linha = f"\r[{barra}] {fracao * 100:3.0f}%  {etapa:<24}{decorrido:5.0f}s"
        print(linha, end="", file=self.saida, flush=True)

    def fim(self) -> None:
        if self.ativa and not self.silenciosa:
            print("", file=self.saida, flush=True)


class FormatoNaoSuportado(ValueError):
    """Extensão de saída que a CLI não sabe gravar."""


def formato_de(saida: Path, pedido: str | None) -> str:
    """Formato final: o pedido em `--format`, ou o da extensão (sem extensão: wav).

    Extensão conhecida mas não suportada (.ogg, .m4a, .flac...) vira erro: gravar um WAV
    com o nome errado deixaria o usuário com um arquivo que alguns players recusam.
    """
    if pedido:
        return pedido
    ext = saida.suffix.lower().lstrip(".")
    if not ext:
        return "wav"
    if ext not in FORMATOS:
        raise FormatoNaoSuportado(
            f"não sei gravar .{ext}; use .wav ou .mp3 (ou passe --format wav|mp3)"
        )
    return ext


def converter(wav: Path, saida: Path, formato: str) -> Path:
    """WAV → formato final. MP3 em VBR qualidade 2 (~190 kbps estéreo, ~90 mono).

    Grava num temporário e só então move para o destino: um erro do ffmpeg (ou um Ctrl+C
    no meio) não pode destruir o arquivo que já estava lá nem deixar um mp3 truncado.
    """
    if formato == "wav":
        if wav != saida:
            shutil.copyfile(wav, saida)
        return saida
    saida.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="audio_fmt_") as tmp:
        parcial = Path(tmp) / f"saida.{formato}"
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
        cmd += ["-i", str(wav), "-c:a", "libmp3lame", "-q:a", "2", str(parcial)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0 or not parcial.exists():
            detalhe = (proc.stderr or "").strip().splitlines()
            motivo = detalhe[-1] if detalhe else "sem detalhe do ffmpeg"
            raise RuntimeError(f"falhou ao gravar {saida.name} em {formato}: {motivo}")
        shutil.move(str(parcial), str(saida))
    return saida


def _linha(nome: str, m: Medidas, lufs: float) -> str:
    return f"{nome:<8}{m.fala_db:>9.1f}{m.ruido_db:>10.1f}{m.snr:>8.1f}{lufs:>10.1f}"


def resumo(r: ResultadoAudio, saida: Path) -> str:
    """Tabela antes/depois + a frase que resume o ganho."""
    linhas = [
        f"{'':<8}{'fala':>9}{'ruído':>10}{'SNR':>8}{'volume':>10}",
        f"{'':<8}{'dBFS':>9}{'dBFS':>10}{'dB':>8}{'LUFS':>10}",
        _linha("antes", r.antes, r.lufs_antes),
        _linha("depois", r.depois, r.lufs_depois),
        "",
        f"SNR +{r.ganho_snr_db:.1f} dB (motor {r.motor})"
        + (" · do cache" if r.do_cache else "")
        + f" → {saida}",
    ]
    return "\n".join(linhas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.audio.optimize",
        description="Limpa o ruído e normaliza o volume de um áudio ou vídeo.",
    )
    parser.add_argument("entrada", type=Path, help="vídeo ou áudio (mp4, mp3, wav, m4a...)")
    parser.add_argument(
        "saida", type=Path, help="arquivo de saída .wav ou .mp3 (sempre mono, 48 kHz)"
    )
    padroes = AudioParams.do_env()
    parser.add_argument(
        "--aggressiveness",
        type=float,
        default=padroes.aggressiveness,
        help=f"0 não limpa, 1 limpa ao máximo (padrão {padroes.aggressiveness}, do .env)",
    )
    parser.add_argument("--format", choices=FORMATOS, help="formato da saída (padrão: extensão)")
    parser.add_argument(
        "--lufs",
        type=float,
        default=padroes.alvo_lufs,
        help=f"volume alvo em LUFS (padrão {padroes.alvo_lufs}, do .env)",
    )
    parser.add_argument("--motor", help="forçar deepfilternet, noisereduce ou afftdn")
    parser.add_argument("--sem-normalizar", action="store_true", help="não ajusta o volume")
    parser.add_argument("--sem-cache", action="store_true", help="refaz mesmo se já houver cache")
    parser.add_argument("-q", "--quieto", action="store_true", help="sem barra de progresso")
    args = parser.parse_args(argv)

    setup_logging(level="WARNING" if not args.quieto else "ERROR")
    for fluxo in (sys.stdout, sys.stderr):  # pipes no Windows usam cp1252 por padrão
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")

    if not args.entrada.is_file():
        print(f"arquivo não encontrado: {args.entrada}", file=sys.stderr)
        return 1
    try:
        params = AudioParams(
            aggressiveness=args.aggressiveness,
            alvo_lufs=args.lufs,
            normalizar=not args.sem_normalizar,
            motor=args.motor,
        )
    except ValueError as exc:
        print(f"parâmetro inválido: {exc}", file=sys.stderr)
        return 2

    try:
        formato = formato_de(args.saida, args.format)
    except FormatoNaoSuportado as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    barra = Barra(ativa=not args.quieto)
    try:
        with tempfile.TemporaryDirectory(prefix="audio_cli_") as tmp:
            wav = Path(tmp) / "limpo.wav" if formato != "wav" else args.saida
            resultado = optimize_audio(
                args.entrada, wav, params, use_cache=not args.sem_cache, on_step=barra
            )
            converter(wav, args.saida, formato)
        barra.fim()
        print(resumo(resultado, args.saida))
    except KeyboardInterrupt:
        barra.fim()
        print("cancelado", file=sys.stderr)
        return 130
    except BrokenPipeError:  # `| head` fecha o cano: não é erro do usuário
        return 0
    except Exception as exc:  # noqa: BLE001 - a CLI traduz qualquer falha
        barra.fim()
        print(mensagem_amigavel(exc), file=sys.stderr)
        log.debug("Falha na CLI de áudio", exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
