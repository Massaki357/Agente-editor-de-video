"""Estilo visual das legendas de destaque, independente da legenda contínua."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from src.highlight_captions.planner import DURACAO_PERMANENCIA_PADRAO


class HighlightStyle(BaseModel):
    """Estilo de destaque na base 1080x1920."""

    fonte: str = "Poppins"
    tamanho: int = Field(72, ge=32, le=160)
    cor: str = Field("#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    cor_entrada: str = Field("#FFD400", pattern=r"^#[0-9A-Fa-f]{6}$")
    cor_contorno: str = Field("#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    contorno: float = Field(5.0, ge=0, le=20)
    sombra: float = Field(2.0, ge=0, le=20)
    margem_lateral: int = Field(80, ge=0, le=400)
    duracao_permanencia: float = Field(DURACAO_PERMANENCIA_PADRAO, ge=0, le=5)
    fade_saida: float = Field(0.25, ge=0, le=1)

    @field_validator("fonte")
    @classmethod
    def _fonte_disponivel(cls, fonte: str) -> str:
        from src.captions import ARQUIVO_FONTE, FONTS_DIR

        if fonte not in ARQUIVO_FONTE or not (FONTS_DIR / ARQUIVO_FONTE[fonte]).is_file():
            raise ValueError(f"fonte '{fonte}' indisponível; disponíveis: {sorted(ARQUIVO_FONTE)}")
        return fonte

    def for_output(self, saida: tuple[int, int]) -> HighlightStyle:
        """Escala geometria do estilo para a altura da saída."""
        escala = saida[1] / 1920
        if abs(escala - 1) < 1e-6:
            return self
        return self.model_copy(
            update={
                "tamanho": max(12, round(self.tamanho * escala)),
                "contorno": round(self.contorno * escala, 2),
                "sombra": round(self.sombra * escala, 2),
                "margem_lateral": round(self.margem_lateral * escala),
            }
        )
