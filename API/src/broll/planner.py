"""Planeja cutaways em frases inteiras da transcrição global.

O LLM escolhe o assunto e a frase; o código fixa as bordas nas palavras reais,
limita duração/densidade e impede sobreposição com fotos e outros cutaways.
Busca e render dos vídeos pertencem às etapas seguintes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.cache import read_json_cache, write_json_cache
from src.config import Settings, get_settings
from src.llm.schemas import BrollSugerido, PlanoBroll

if TYPE_CHECKING:
    from src.images import PalavraGlobal, PlanoImagens
    from src.project import Project

log = logging.getLogger(__name__)
PLANO_BROLL_VERSION = 1


class BrollParams(BaseModel):
    """Limites aplicados mesmo quando o LLM sugere algo fora das regras."""

    model_config = ConfigDict(allow_inf_nan=False)

    intervalo_min: float = Field(8.0, ge=0, description="s mínimos entre inícios")
    duracao_min: float = Field(1.5, gt=0)
    duracao_max: float = Field(4.0, gt=0)
    retorno_min: float = Field(1.0, ge=0, description="s de câmera entre cutaways")
    pausa_frase: float = Field(0.65, gt=0, description="pausa que separa frases")

    @model_validator(mode="after")
    def _limites(self) -> BrollParams:
        if self.duracao_max < self.duracao_min:
            raise ValueError("duracao_max precisa ser maior que duracao_min")
        return self


class TrechoFala(BaseModel):
    """Frase ou oração candidata com bordas no começo e fim exatos de palavras."""

    clipe: int
    trecho_inicio_palavra: int
    trecho_fim_palavra: int
    inicio: float  # t_out
    fim: float  # t_out
    texto: str

    @property
    def duracao(self) -> float:
        return self.fim - self.inicio


class VideoBroll(BaseModel):
    """Clipe escolhido na prévia; reutilizado no render sem nova busca."""

    arquivo: Path
    fonte: Literal["pexels", "pixabay"]
    id: str
    pagina: str
    autor: str = ""


class ItemBroll(BaseModel):
    """Cutaway validado e pronto para a busca de vídeo da Etapa 1."""

    id: int
    clipe: int
    trecho_inicio_palavra: int
    trecho_fim_palavra: int
    texto: str
    query: str
    inicio: float  # t_out, sempre início de palavra
    duracao_max: float  # termina no fim da frase, nunca no meio de palavra
    motivo: str
    ativo: bool = True
    aprovado: bool = False
    video: VideoBroll | None = None

    @property
    def fim(self) -> float:
        return self.inicio + self.duracao_max


def segmentar_fala(
    palavras: Sequence[PalavraGlobal], params: BrollParams | None = None
) -> list[TrechoFala]:
    """Agrupa palavras por pontuação, pausa e clipe; não atravessa uma emenda."""
    params = params or BrollParams()
    if not palavras:
        return []
    trechos: list[TrechoFala] = []
    primeiro = 0
    for i, atual in enumerate(palavras):
        proximo = palavras[i + 1] if i + 1 < len(palavras) else None
        texto = atual.palavra.texto.strip()
        duracao_parcial = atual.palavra.fim - palavras[primeiro].palavra.inicio
        terminou = (
            proximo is None
            or proximo.clipe != atual.clipe
            or proximo.segmento != atual.segmento
            or bool(re.search(r"[.!?;:]$", texto))
            or (texto.endswith(",") and duracao_parcial >= params.duracao_min)
            or (proximo.palavra.inicio - atual.palavra.fim >= params.pausa_frase)
        )
        if not terminou:
            continue
        grupo = palavras[primeiro : i + 1]
        trechos.append(
            TrechoFala(
                clipe=atual.clipe,
                trecho_inicio_palavra=grupo[0].indice,
                trecho_fim_palavra=grupo[-1].indice,
                inicio=grupo[0].palavra.inicio,
                fim=grupo[-1].palavra.fim,
                texto=" ".join(g.palavra.texto for g in grupo),
            )
        )
        primeiro = i + 1
    return trechos


def _sobrepoe(a: float, b: float, c: float, d: float) -> bool:
    return a < d - 1e-6 and c < b - 1e-6


def _imagens_ativas(plano: PlanoImagens) -> list[tuple[float, float]]:
    return [(i.inicio, i.fim) for i in plano.itens if i.ativa]


def suggest_broll(
    trechos: Sequence[TrechoFala],
    imagens: Sequence[tuple[float, float]],
    params: BrollParams | None = None,
    settings: Settings | None = None,
) -> list[BrollSugerido]:
    """Sugestões do LLM com cache; falha devolve lista vazia."""
    params = params or BrollParams()
    candidatos = [
        t
        for t in trechos
        if params.duracao_min <= t.duracao <= params.duracao_max
        and not any(_sobrepoe(t.inicio, t.fim, a, b) for a, b in imagens)
    ]
    if not candidatos:
        return []
    settings = settings or get_settings()
    prompt = (Path(__file__).parent.parent / "llm" / "prompts" / "plano_broll.md").read_text(
        encoding="utf-8"
    )
    entrada = {
        "trechos": [t.model_dump() for t in candidatos],
        "imagens_sobrepostas": [{"inicio": a, "fim": b} for a, b in imagens],
        "limites": {
            "duracao_min": params.duracao_min,
            "duracao_max": params.duracao_max,
            "intervalo_min": params.intervalo_min,
        },
    }
    chave = hashlib.sha256(
        json.dumps(
            [PLANO_BROLL_VERSION, settings.llm_model, prompt, entrada], ensure_ascii=False
        ).encode()
    ).hexdigest()[:40]
    cached = read_json_cache("llm_broll", chave, cache_dir=settings.cache_dir)
    if cached is not None:
        return [BrollSugerido.model_validate(item) for item in cached]

    from src.llm import client

    try:
        resposta = client.run_structured("plano_broll", entrada, PlanoBroll, settings=settings)
    except client.LLMError as exc:
        log.warning("Planejamento de B-roll sem LLM (%s): nenhum cutaway.", exc)
        return []
    write_json_cache(
        "llm_broll", chave, [item.model_dump() for item in resposta.broll], settings.cache_dir
    )
    return resposta.broll


def validate_broll(
    sugestoes: Sequence[BrollSugerido],
    trechos: Sequence[TrechoFala],
    imagens: Sequence[tuple[float, float]] = (),
    params: BrollParams | None = None,
) -> list[ItemBroll]:
    """Só aceita frases inteiras; respeita duração, densidade e exclusividade."""
    params = params or BrollParams()
    validos = {
        (t.trecho_inicio_palavra, t.trecho_fim_palavra): t
        for t in trechos
        if params.duracao_min <= t.duracao <= params.duracao_max
    }
    resultado: list[ItemBroll] = []
    for bruto in sorted(sugestoes, key=lambda s: (s.trecho_inicio_palavra, s.trecho_fim_palavra)):
        trecho = validos.get((bruto.trecho_inicio_palavra, bruto.trecho_fim_palavra))
        if trecho is None or not bruto.query.strip() or not math.isfinite(bruto.duracao_max):
            continue
        if bruto.duracao_max < params.duracao_min:
            continue
        if any(_sobrepoe(trecho.inicio, trecho.fim, a, b) for a, b in imagens):
            continue
        # A frase inteira fica na tela. A duração pedida pelo LLM é só uma sugestão;
        # encurtá-la faria a câmera voltar no meio da frase.
        if resultado and (
            trecho.inicio < resultado[-1].inicio + params.intervalo_min
            or trecho.inicio < resultado[-1].fim + params.retorno_min
        ):
            continue
        resultado.append(
            ItemBroll(
                id=len(resultado),
                clipe=trecho.clipe,
                trecho_inicio_palavra=trecho.trecho_inicio_palavra,
                trecho_fim_palavra=trecho.trecho_fim_palavra,
                texto=trecho.texto,
                query=bruto.query.strip(),
                inicio=trecho.inicio,
                duracao_max=trecho.duracao,
                motivo=bruto.motivo.strip(),
            )
        )
    return resultado


def plan_broll(
    palavras: Sequence[PalavraGlobal],
    plano: PlanoImagens,
    params: BrollParams | None = None,
    settings: Settings | None = None,
) -> PlanoImagens:
    """Anexa cutaways validados ao plano criativo já existente."""
    params = params or BrollParams()
    trechos = segmentar_fala(palavras, params)
    imagens = _imagens_ativas(plano)
    sugestoes = suggest_broll(trechos, imagens, params, settings)
    atualizado = plano.model_copy(deep=True)
    atualizado.broll = validate_broll(sugestoes, trechos, imagens, params)
    log.info("Plano de B-roll: %d cutaway(s) em %d frase(s).", len(atualizado.broll), len(trechos))
    return atualizado


def plan_broll_project(
    project: Project,
    plano: PlanoImagens,
    transcriber: Callable[[Path], object],
    params: BrollParams | None = None,
    settings: Settings | None = None,
) -> PlanoImagens:
    """Entrada para o pipeline: usa as palavras globais já cortadas, em t_out."""
    from src.images import global_words

    return plan_broll(global_words(project, transcriber), plano, params, settings)
