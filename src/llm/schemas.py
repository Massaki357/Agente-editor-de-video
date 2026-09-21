"""Modelos Pydantic das saídas estruturadas do LLM.

Todos os campos são obrigatórios e sem valores padrão, para funcionar com o modo
estrito de JSON schema dos provedores. A validação semântica (índices existem,
limites de densidade etc.) é feita em código por quem consome o resultado.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CorteFala(BaseModel):
    """Intervalo de palavras a remover (índices inclusivos)."""

    indice_inicio: int = Field(description="índice da primeira palavra a remover")
    indice_fim: int = Field(description="índice da última palavra a remover (inclusivo)")
    motivo: str = Field(
        description="motivo curto: falso começo, repetição, take errado ou hesitação"
    )


class CortesFala(BaseModel):
    """Resposta do prompt `cortes_fala`: lista vazia quando não há nada a cortar."""

    cortes: list[CorteFala]
