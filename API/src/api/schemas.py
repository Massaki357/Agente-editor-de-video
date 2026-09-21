"""Modelos de entrada e saída da API (o contrato que o frontend usa)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.pipeline import PipelineOptions


class ProjectCreate(BaseModel):
    nome: str = Field("Novo projeto", max_length=120)


class ProjectRename(BaseModel):
    nome: str = Field(..., min_length=1, max_length=120)


class ImportFolder(BaseModel):
    pasta: str = Field(..., description="caminho de uma pasta local com os vídeos")


class Reorder(BaseModel):
    ordem: list[int] = Field(..., description="nova ordem, pelos índices atuais")


class JobCreate(BaseModel):
    tipo: Literal["transcrever", "rosto", "gerar"]
    opcoes: PipelineOptions | None = Field(
        None, description="só para 'gerar': cortes, cortes_fala, min_silencio, margem, ruido_db"
    )


class ClipOut(BaseModel):
    indice: int
    nome: str
    arquivo: str
    duracao: float | None
    largura: int | None
    altura: int | None
    fps: float | None
    tem_audio: bool | None
    trechos: list[tuple[float, float]]
    offset: float
    duracao_mantida: float
    transcrito: bool
    rosto: bool
    video_url: str
    thumbnail_url: str


class ProjectSummary(BaseModel):
    id: str
    nome: str
    criado: datetime
    atualizado: datetime
    n_clipes: int
    video_final_url: str | None


class ProjectOut(ProjectSummary):
    clipes: list[ClipOut]
    duracao_total: float
    arquivos: list[str] = Field(description="arquivos gerados em saida/")
    job_ativo: str | None


class Palavra(BaseModel):
    indice: int
    texto: str
    inicio: float
    fim: float
    prob: float


class TranscricaoOut(BaseModel):
    modelo: str
    duracao: float
    texto: str
    palavras: list[Palavra]


class RostoOut(BaseModel):
    fps: float
    largura: int
    altura: int
    n_frames: int
    cobertura: float
    debug_url: str | None


class CheckOut(BaseModel):
    nome: str
    status: Literal["OK", "AVISO", "FALTA"]
    detalhe: str


class ConfigOut(BaseModel):
    llm_model: str
    whisper_model: str
    whisper_device: str
    saida: dict[str, int]
    opcoes_padrao: PipelineOptions
