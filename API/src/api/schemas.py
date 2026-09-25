"""Modelos de entrada e saída da API (o contrato que o frontend usa)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.highlight_captions.style import HighlightStyle
from src.images import PlanoImagens
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
    tipo: Literal["transcrever", "rosto", "imagens", "broll", "gerar"]
    opcoes: PipelineOptions | None = Field(
        None, description="para 'rosto', 'imagens', 'broll' e 'gerar': opções do pipeline"
    )
    ids_alterados: list[str] = Field(default_factory=list)


class ClipOut(BaseModel):
    indice: int
    nome: str
    arquivo: str
    duracao: float | None
    largura: int | None
    altura: int | None
    fps: float | None
    tem_audio: bool | None
    vfr: bool = False  # fps variável (aviso na interface)
    hdr: bool = False  # HDR convertido para SDR no render
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
    estabilizar: bool = False
    suavizacao_estabilizacao: Literal["leve", "medio", "forte"] = "medio"
    broll: bool = False
    broll_intervalo_min: float = 8.0
    broll_transition: Literal["hard_cut", "crossfade", "slide", "wipe"] = "hard_cut"
    legendas_continuas: bool = True
    legendas_destaque: bool = False
    estilo_destaque: HighlightStyle = Field(default_factory=HighlightStyle)
    pode_substituir_imagens: bool = False
    pode_substituir_broll: bool = False
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
    llm_model: str  # o do .env (padrão quando `opcoes.llm_model` é nulo)
    llm_models: list[str] = Field(description="modelos que podem ser escolhidos nas opções")
    whisper_model: str
    whisper_device: str
    saida: dict[str, int]
    opcoes_padrao: PipelineOptions


class ImagemEdit(BaseModel):
    """Mudança num item do plano de imagens (nenhuma delas chama o LLM)."""

    escolhida: int | None = Field(None, ge=0, description="índice do candidato a usar")
    ativa: bool | None = None
    query: str | None = Field(None, min_length=2, max_length=80, description="nova busca")


class ZoomEdit(BaseModel):
    ativo: bool


class PlanoOut(BaseModel):
    valido: bool = Field(description="False se os trechos mudaram desde o plano")
    plano: PlanoImagens


class BrollEdit(BaseModel):
    """Decisão de prévia; nova busca acontece no job, nunca nesta requisição."""

    query: str | None = Field(None, min_length=2, max_length=80)
    ativo: bool | None = None
    aprovado: bool | None = None


class BrollItemOut(BaseModel):
    id: int
    texto: str
    query: str
    inicio: float
    duracao: float
    ativo: bool
    aprovado: bool
    fonte: str | None = None
    autor: str | None = None
    pagina: str | None = None
    video_url: str | None = None
    video_id: str | None = None
    alternativas: list[dict] = Field(default_factory=list)


class BrollPreviewOut(BaseModel):
    valido: bool
    itens: list[BrollItemOut]
