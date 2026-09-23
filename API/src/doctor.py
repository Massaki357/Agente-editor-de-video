"""Verificação do ambiente: `python -m src.doctor`.

Cada checagem é isolada: se algo falta ou lança exceção, o item é marcado e as
demais continuam. Sai com código 0, ou 1 com `--strict` quando há falhas.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import logging
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from pydantic import ValidationError

from src.config import Settings, get_settings
from src.logging_setup import setup_logging

log = logging.getLogger(__name__)


class Status(Enum):
    OK = "OK"
    WARN = "AVISO"
    FAIL = "FALTA"


@dataclass
class Check:
    name: str
    status: Status
    detail: str = ""


# (nome no PyPI, módulo importável)
PACKAGES = [
    ("faster-whisper", "faster_whisper"),
    ("mediapipe", "mediapipe"),
    ("opencv-python", "cv2"),
    ("numpy", "numpy"),
    ("pydantic", "pydantic"),
    ("python-dotenv", "dotenv"),
    ("natsort", "natsort"),
    ("langchain", "langchain"),
    ("langchain-core", "langchain_core"),
    ("langchain-openai", "langchain_openai"),
    ("langchain-anthropic", "langchain_anthropic"),
    ("requests", "requests"),
    ("rembg", "rembg"),
    ("noisereduce", "noisereduce"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("pytest", "pytest"),
]

REQUIRED_FFMPEG_FILTERS = ["ass", "silencedetect", "overlay", "afade", "concat"]

# Filtros usados pelo otimizador de áudio (Parte 1): RNNoise e ruído por FFT são as
# alternativas quando o DeepFilterNet não está disponível.
AUDIO_FFMPEG_FILTERS = ("highpass", "loudnorm", "arnndn", "afftdn")


def _run(cmd: list[str], timeout: float = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )


def check_python() -> list[Check]:
    v = sys.version_info
    ok = (3, 11) <= (v.major, v.minor)
    return [
        Check(
            "Python",
            Status.OK if ok else Status.FAIL,
            f"{v.major}.{v.minor}.{v.micro}" + ("" if ok else " (precisa 3.11+)"),
        )
    ]


def check_binary(name: str) -> Check:
    path = shutil.which(name)
    if not path:
        return Check(name, Status.FAIL, "não encontrado no PATH")
    first_line = _run([name, "-version"]).stdout.splitlines()
    return Check(name, Status.OK, first_line[0] if first_line else path)


def _ffmpeg_filters() -> set[str] | None:
    """Nomes dos filtros do FFmpeg instalado; None se o FFmpeg não responder."""
    try:
        out = _run(["ffmpeg", "-hide_banner", "-filters"]).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return {parts[1] for line in out.splitlines() if len(parts := line.split()) >= 2}


def check_ffmpeg() -> list[Check]:
    checks = [check_binary("ffmpeg"), check_binary("ffprobe")]
    if checks[0].status is Status.OK:
        available = _ffmpeg_filters() or set()
        missing = [f for f in REQUIRED_FFMPEG_FILTERS if f not in available]
        checks.append(
            Check(
                "Filtros FFmpeg",
                Status.FAIL if missing else Status.OK,
                f"faltando: {', '.join(missing)}"
                if missing
                else ", ".join(REQUIRED_FFMPEG_FILTERS),
            )
        )
    return checks


def check_packages() -> list[Check]:
    checks = []
    for dist, module in PACKAGES:
        if importlib.util.find_spec(module) is None:
            checks.append(Check(f"pacote {dist}", Status.FAIL, "não instalado (rode `uv sync`)"))
            continue
        try:
            version = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            version = "?"
        checks.append(Check(f"pacote {dist}", Status.OK, version))
    return checks


def check_gpu(settings: Settings) -> list[Check]:
    checks = []

    if shutil.which("nvidia-smi"):
        out = _run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
        gpus = [line.strip() for line in out.stdout.splitlines() if line.strip()]
        checks.append(
            Check("GPU NVIDIA", Status.OK if gpus else Status.WARN, "; ".join(gpus) or "nenhuma")
        )
    else:
        checks.append(Check("GPU NVIDIA", Status.WARN, "nvidia-smi não encontrado; usando CPU"))

    # O que importa para o Whisper é o CTranslate2 enxergar a GPU.
    if importlib.util.find_spec("ctranslate2") is None:
        checks.append(Check("CUDA p/ Whisper", Status.WARN, "ctranslate2 não instalado"))
    else:
        import ctranslate2

        count = ctranslate2.get_cuda_device_count()
        if count:
            types = sorted(ctranslate2.get_supported_compute_types("cuda"))
            detail = f"{count} dispositivo(s); compute types: {', '.join(types)}"
            checks.append(Check("CUDA p/ Whisper", Status.OK, detail))
        else:
            status = Status.FAIL if settings.whisper_device == "cuda" else Status.WARN
            checks.append(
                Check("CUDA p/ Whisper", status, "nenhum dispositivo CUDA; Whisper roda na CPU")
            )

    checks.append(Check("WHISPER_DEVICE", Status.OK, settings.whisper_device))
    return checks


def check_keys(settings: Settings) -> list[Check]:
    provider = settings.llm_provider
    checks = [
        Check(
            "LLM_MODEL",
            Status.OK if provider else Status.WARN,
            settings.llm_model + ("" if provider else " (esperado provedor:modelo)"),
        )
    ]

    if provider in ("openai", "anthropic"):
        key = settings.api_key_for_provider(provider)
        env = f"{provider.upper()}_API_KEY"
        checks.append(Check(env, Status.OK if key else Status.FAIL, "definida" if key else "vazia"))
    else:
        checks.append(
            Check(
                "Chave do LLM",
                Status.WARN,
                f"provedor '{provider or '?'}' não reconhecido; use o formato provedor:modelo",
            )
        )

    for env, key, detail_missing in [
        ("PEXELS_API_KEY", settings.pexels_api_key, "vazia (busca principal de imagens)"),
        ("PIXABAY_API_KEY", settings.pixabay_api_key, "vazia (fallback de imagens)"),
    ]:
        checks.append(
            Check(env, Status.OK if key else Status.WARN, "definida" if key else detail_missing)
        )
    return checks


def check_face_model(settings: Settings) -> list[Check]:
    from src.face import MODEL_NAME

    path = settings.cache_dir / "models" / MODEL_NAME
    if path.exists():
        return [Check("Modelo de rosto", Status.OK, str(path))]
    return [Check("Modelo de rosto", Status.WARN, "será baixado no 1º uso (~230 KB)")]


def check_audio(settings: Settings) -> list[Check]:
    """Otimizador de áudio: DeepFilterNet (binário) e os filtros de áudio do FFmpeg."""
    from src.audio import deepfilter

    checks: list[Check] = []
    asset = deepfilter.asset_da_plataforma()
    path = deepfilter.binary_path(settings)
    if asset is None:
        checks.append(
            Check(
                "DeepFilterNet",
                Status.WARN,
                f"sem binário para {platform.system()}/{platform.machine()}; "
                "a limpeza cai para o RNNoise do FFmpeg ou o noisereduce",
            )
        )
    elif deepfilter.available(settings):
        checks.append(Check("DeepFilterNet", Status.OK, f"v{deepfilter.VERSAO} em {path}"))
    else:
        checks.append(
            Check("DeepFilterNet", Status.WARN, f"será baixado no 1º uso (~27 MB, {asset})")
        )

    filtros = _ffmpeg_filters()
    if filtros is None:
        checks.append(Check("Filtros de áudio", Status.WARN, "FFmpeg indisponível para checar"))
    else:
        faltando = [f for f in AUDIO_FFMPEG_FILTERS if f not in filtros]
        checks.append(
            Check(
                "Filtros de áudio",
                Status.WARN if faltando else Status.OK,
                f"faltando: {', '.join(faltando)}" if faltando else ", ".join(AUDIO_FFMPEG_FILTERS),
            )
        )
    return checks


def check_cache_dir(settings: Settings) -> list[Check]:
    try:
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        probe = settings.cache_dir / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return [Check("CACHE_DIR", Status.OK, str(settings.cache_dir))]
    except OSError as exc:
        return [Check("CACHE_DIR", Status.FAIL, f"{settings.cache_dir}: {exc}")]


def run_checks(settings: Settings) -> list[Check]:
    groups: list[tuple[str, Callable[[], list[Check]]]] = [
        ("Python", check_python),
        ("FFmpeg", check_ffmpeg),
        ("Pacotes", check_packages),
        ("GPU", lambda: check_gpu(settings)),
        ("Chaves de API", lambda: check_keys(settings)),
        ("Áudio", lambda: check_audio(settings)),
        ("Cache", lambda: check_cache_dir(settings)),
        ("Modelo de rosto", lambda: check_face_model(settings)),
    ]
    results: list[Check] = []
    for label, fn in groups:
        try:
            results.extend(fn())
        except Exception as exc:  # uma checagem com erro não derruba o doctor
            log.debug("Falha na checagem %s", label, exc_info=True)
            results.append(Check(label, Status.FAIL, f"erro ao verificar: {exc}"))
    return results


def print_report(checks: list[Check]) -> None:
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{c.status.value:<5}] {c.name:<{width}}  {c.detail}")
    fails = sum(c.status is Status.FAIL for c in checks)
    warns = sum(c.status is Status.WARN for c in checks)
    print(f"\n{len(checks)} itens: {fails} faltando, {warns} avisos.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.doctor", description=__doc__)
    parser.add_argument("--strict", action="store_true", help="sai com código 1 se algo faltar")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):  # pipes no Windows usam cp1252 por padrão
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    checks: list[Check] = []
    try:
        settings = get_settings()
        checks.append(Check("Configuração (.env)", Status.OK, "válida"))
    except ValidationError as exc:
        erros = "; ".join(
            f"{'.'.join(map(str, e['loc'])).upper()}: {e['msg']}" for e in exc.errors()
        )
        checks.append(Check("Configuração (.env)", Status.FAIL, f"{erros} (usando padrões)"))
        settings = Settings()

    setup_logging(settings.log_dir, settings.log_level)
    checks += run_checks(settings)
    print_report(checks)
    return 1 if args.strict and any(c.status is Status.FAIL for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
