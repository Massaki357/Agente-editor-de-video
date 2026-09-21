"""Saúde, configuração e verificação do ambiente."""

from __future__ import annotations

from fastapi import APIRouter

from src import doctor
from src.api.schemas import CheckOut, ConfigOut
from src.config import get_settings
from src.pipeline import PipelineOptions

router = APIRouter(tags=["sistema"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/config", response_model=ConfigOut)
def config() -> ConfigOut:
    s = get_settings()
    return ConfigOut(
        llm_model=s.llm_model,
        whisper_model=s.whisper_model,
        whisper_device=s.whisper_device,
        saida={"largura": s.output_width, "altura": s.output_height, "fps": s.output_fps},
        opcoes_padrao=PipelineOptions(),
    )


@router.get("/doctor", response_model=list[CheckOut])
def run_doctor() -> list[CheckOut]:
    """As mesmas checagens de `python -m src.doctor` (FFmpeg, GPU, pacotes, chaves)."""
    return [
        CheckOut(nome=c.name, status=c.status.value, detalhe=c.detail)
        for c in doctor.run_checks(get_settings())
    ]
