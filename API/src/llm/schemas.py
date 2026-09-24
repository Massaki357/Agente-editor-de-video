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


class ImagemSugerida(BaseModel):
    """Uma imagem para aparecer sobre a fala, ancorada numa palavra da transcrição."""

    indice: int = Field(description="índice (global) da palavra que a imagem ilustra")
    palavra: str = Field(description="a palavra desse índice, copiada da transcrição")
    query: str = Field(description="busca em inglês para um banco de fotos (2 a 4 palavras)")
    duracao: float = Field(description="segundos na tela, entre 1.2 e 3.0")


class ZoomSugerido(BaseModel):
    """Um zoom no rosto para dar ênfase a um momento forte da fala."""

    indice: int = Field(description="índice (global) da palavra em que o zoom começa")
    palavra: str = Field(description="a palavra desse índice, copiada da transcrição")
    duracao: float = Field(description="segundos de zoom, entre 1.0 e 2.5")


class BrollSugerido(BaseModel):
    """Trecho de fala inteiro que merece um cutaway de vídeo."""

    trecho_inicio_palavra: int = Field(description="índice global da primeira palavra da frase")
    trecho_fim_palavra: int = Field(description="índice global da última palavra da frase")
    query: str = Field(description="busca em inglês para vídeo relacionado à frase")
    duracao_max: float = Field(description="duração máxima desejada em segundos")
    motivo: str = Field(description="por que o vídeo agrega à frase")


class PlanoBroll(BaseModel):
    """Resposta do prompt `plano_broll`; lista vazia quando não há cutaways úteis."""

    broll: list[BrollSugerido]


class PlanoCriativo(BaseModel):
    """Resposta do prompt `plano_criativo`: listas vazias quando nada merece destaque."""

    imagens: list[ImagemSugerida]
    zooms: list[ZoomSugerido]
