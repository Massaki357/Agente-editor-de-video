"""DeepFilterNet pelo binário oficial do projeto (Parte 1, Etapa 0).

Por que o binário e não o pacote `deepfilternet` do PyPI: o `deepfilterlib` só publica
wheels até o Python 3.11, e este projeto roda no 3.12 — sem wheel, o pip tenta compilar
Rust. O binário oficial (um arquivo, ~27 MB) tem o mesmo modelo, roda em CPU, não precisa
de torch e é baixado uma vez para o `CACHE_DIR/models/`, como o modelo de rosto.

O binário aceita só WAV; quem chama converte antes (`ffmpeg`). Para não desalinhar com o
vídeo, sempre usamos `--compensate-delay`, que devolve o áudio com a mesma duração.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

from src.config import Settings, get_settings

log = logging.getLogger(__name__)

VERSAO = "0.5.6"
TAMANHO_MIN = 5_000_000  # um download truncado não passa disso
TIMEOUT_DOWNLOAD = 300
# (sistema, arquitetura) → nome do arquivo na release do GitHub
ASSETS = {
    ("Windows", "AMD64"): f"deep-filter-{VERSAO}-x86_64-pc-windows-msvc.exe",
    ("Linux", "x86_64"): f"deep-filter-{VERSAO}-x86_64-unknown-linux-musl",
    ("Linux", "aarch64"): f"deep-filter-{VERSAO}-aarch64-unknown-linux-gnu",
    ("Darwin", "arm64"): f"deep-filter-{VERSAO}-aarch64-apple-darwin",
    ("Darwin", "x86_64"): f"deep-filter-{VERSAO}-x86_64-apple-darwin",
}
URL_BASE = f"https://github.com/Rikorose/DeepFilterNet/releases/download/v{VERSAO}"


class DeepFilterIndisponivel(RuntimeError):
    """Não há binário para esta plataforma, ou o download falhou."""


def asset_da_plataforma() -> str | None:
    """Nome do arquivo da release para esta máquina; None se não houver."""
    return ASSETS.get((platform.system(), platform.machine()))


def binary_path(settings: Settings | None = None) -> Path | None:
    """Onde o binário fica (ou ficaria) no cache; None em plataforma sem suporte."""
    asset = asset_da_plataforma()
    if asset is None:
        return None
    settings = settings or get_settings()
    sufixo = ".exe" if asset.endswith(".exe") else ""
    return settings.cache_dir / "models" / f"deep-filter-{VERSAO}{sufixo}"


def available(settings: Settings | None = None) -> bool:
    """Já baixado e pronto para usar (não baixa nada)."""
    path = binary_path(settings)
    return path is not None and path.is_file() and path.stat().st_size >= TAMANHO_MIN


def ensure_binary(settings: Settings | None = None, baixar: bool = True) -> Path:
    """Caminho do binário, baixando na primeira vez. Erro claro se não der."""
    path = binary_path(settings)
    if path is None:
        raise DeepFilterIndisponivel(
            f"não há binário do DeepFilterNet para {platform.system()}/{platform.machine()}; "
            "o otimizador usa o RNNoise do FFmpeg ou o noisereduce"
        )
    if available(settings):
        return path
    if not baixar:
        raise DeepFilterIndisponivel(f"DeepFilterNet ainda não baixado ({path})")

    asset = asset_da_plataforma()
    url = f"{URL_BASE}/{asset}"
    log.info("Baixando o DeepFilterNet (%s, ~27 MB)...", asset)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".part")  # `with_suffix` tropeçaria no "0.5.6"
    try:
        with requests.get(url, timeout=TIMEOUT_DOWNLOAD, stream=True) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as saida:
                for pedaco in resp.iter_content(chunk_size=1 << 20):
                    saida.write(pedaco)
    except requests.RequestException as exc:
        tmp.unlink(missing_ok=True)
        raise DeepFilterIndisponivel(f"falha ao baixar o DeepFilterNet ({url}): {exc}") from exc
    if tmp.stat().st_size < TAMANHO_MIN:
        tmp.unlink(missing_ok=True)
        raise DeepFilterIndisponivel(f"download do DeepFilterNet veio incompleto ({url})")
    tmp.replace(path)
    path.chmod(0o755)
    return path


def enhance(
    entrada: Path,
    saida: Path,
    settings: Settings | None = None,
    *,
    atenuacao_db: float = 100.0,
    pos_filtro: bool = False,
    timeout: float = 900.0,
) -> Path:
    """Roda o DeepFilterNet num WAV e escreve o resultado em `saida`.

    `atenuacao_db` limita o quanto o ruído é atenuado (100 = limpeza total, 0 = nada):
    é o que a Etapa 1 expõe como `aggressiveness`, e evita voz robotizada em áudio
    pouco ruidoso. O binário escreve na pasta `--output-dir` com o nome do arquivo de
    entrada, então usamos uma pasta temporária e movemos para `saida`.
    """
    entrada, saida = Path(entrada), Path(saida)
    if not entrada.is_file():
        raise FileNotFoundError(f"áudio não encontrado: {entrada}")
    binario = ensure_binary(settings)
    saida.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="deepfilter_") as tmp:
        cmd = [str(binario), "--compensate-delay", "--output-dir", tmp]
        cmd += ["--atten-lim-db", f"{max(0.0, min(atenuacao_db, 100.0)):.1f}"]
        if pos_filtro:
            cmd.append("--pf")
        cmd.append(str(entrada))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise DeepFilterIndisponivel(
                f"DeepFilterNet passou de {timeout:.0f} s em {entrada.name}; "
                "divida o áudio em trechos menores"
            ) from exc
        if proc.returncode != 0:
            erro = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise DeepFilterIndisponivel(
                f"DeepFilterNet falhou em {entrada.name}: " + " | ".join(erro[-3:])
            )
        gerado = Path(tmp) / entrada.name
        if not gerado.is_file():  # versões antigas trocavam a extensão
            candidatos = sorted(Path(tmp).glob("*"))
            if not candidatos:
                raise DeepFilterIndisponivel(f"DeepFilterNet não gerou saída para {entrada.name}")
            gerado = candidatos[0]
        shutil.move(str(gerado), str(saida))
    return saida
