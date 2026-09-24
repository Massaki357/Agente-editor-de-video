"""Escolhe trechos curtos de impacto sem alterar o texto nem os tempos."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.cache import read_json_cache, write_json_cache
from src.config import Settings, get_settings
from src.llm.schemas import DestaqueSugerido, PlanoDestaques

if TYPE_CHECKING:
    from src.images import PalavraGlobal, PlanoImagens
    from src.project import Project

log = logging.getLogger(__name__)
PLANO_DESTAQUES_VERSION = 2
DURACAO_PERMANENCIA_PADRAO = 1.2
MAX_PALAVRAS_DESTAQUE = 5
_PONTUACAO_FINAL = re.compile(r"[.!?;:]$|[.!?][\"'”’)]$")


class DestaqueParams(BaseModel):
    """Limites objetivos para aceitar sugestões do LLM."""

    model_config = ConfigDict(allow_inf_nan=False)

    intervalo_min: float = Field(10.0, ge=0, description="s entre inícios de destaques")
    duracao_min: float = Field(0.3, gt=0)
    duracao_max: float = Field(3.5, gt=0)
    pausa_frase: float = Field(0.65, gt=0)
    duracao_permanencia: float = Field(DURACAO_PERMANENCIA_PADRAO, ge=0, le=5)

    @model_validator(mode="after")
    def _limites(self) -> DestaqueParams:
        if self.duracao_max < self.duracao_min:
            raise ValueError("duracao_max precisa ser maior que duracao_min")
        return self


class FraseCandidata(BaseModel):
    """Frase inteira em tempo final (`t_out`) e em um único trecho mantido."""

    clipe: int
    segmento: int
    trecho_inicio_palavra: int
    trecho_fim_palavra: int
    inicio: float
    fim: float
    texto: str

    @property
    def duracao(self) -> float:
        return self.fim - self.inicio


class ItemDestaque(BaseModel):
    """Trecho literal validado, pronto para gerar o .ass."""

    id: int
    clipe: int
    segmento: int
    trecho_inicio_palavra: int
    trecho_fim_palavra: int
    inicio: float
    fim: float
    texto: str
    motivo: str
    ativo: bool = True
    duracao_permanencia: float | None = Field(default=None, ge=0, le=5)


def segmentar_frases(
    palavras: Sequence[PalavraGlobal], params: DestaqueParams | None = None
) -> list[FraseCandidata]:
    """Separa pontuação, pausas e emendas sem inventar fronteiras de palavra."""
    params = params or DestaqueParams()
    if not palavras:
        return []
    frases: list[FraseCandidata] = []
    primeiro = 0
    for i, atual in enumerate(palavras):
        proximo = palavras[i + 1] if i + 1 < len(palavras) else None
        texto = atual.palavra.texto.strip()
        parcial = atual.palavra.fim - palavras[primeiro].palavra.inicio
        terminou = (
            proximo is None
            or proximo.clipe != atual.clipe
            or proximo.segmento != atual.segmento
            or bool(_PONTUACAO_FINAL.search(texto))
            or (texto.endswith(",") and parcial >= params.duracao_min)
            or proximo.palavra.inicio - atual.palavra.fim >= params.pausa_frase
        )
        if not terminou:
            continue
        grupo = palavras[primeiro : i + 1]
        frases.append(
            FraseCandidata(
                clipe=atual.clipe,
                segmento=atual.segmento,
                trecho_inicio_palavra=grupo[0].indice,
                trecho_fim_palavra=grupo[-1].indice,
                inicio=grupo[0].palavra.inicio,
                fim=grupo[-1].palavra.fim,
                texto=" ".join(g.palavra.texto for g in grupo),
            )
        )
        primeiro = i + 1
    return frases


def suggest_highlights(
    frases: Sequence[FraseCandidata],
    params: DestaqueParams | None = None,
    settings: Settings | None = None,
    *,
    palavras: Sequence[PalavraGlobal] | None = None,
) -> list[DestaqueSugerido]:
    """Consulta o LLM uma vez e usa cache por roteiro, limites, prompt e modelo."""
    params = params or DestaqueParams()
    candidatas = [f for f in frases if f.trecho_fim_palavra >= f.trecho_inicio_palavra]
    if not candidatas:
        return []
    settings = settings or get_settings()
    prompt = (Path(__file__).parent.parent / "llm" / "prompts" / "plano_destaques.md").read_text(
        encoding="utf-8"
    )
    entrada = {
        "frases": [f.model_dump() for f in candidatas],
        "roteiro_completo": [f.model_dump() for f in frases],
        "palavras": [
            {"indice": p.indice, "texto": p.palavra.texto}
            for p in (palavras or [])
        ],
        "limites": {
            "intervalo_min": params.intervalo_min,
            "duracao_min": params.duracao_min,
            "duracao_max": params.duracao_max,
            "duracao_permanencia": params.duracao_permanencia,
            "max_palavras": MAX_PALAVRAS_DESTAQUE,
        },
    }
    chave = hashlib.sha256(
        json.dumps(
            [PLANO_DESTAQUES_VERSION, settings.llm_model, prompt, entrada], ensure_ascii=False
        ).encode()
    ).hexdigest()[:40]
    cached = read_json_cache("llm_destaques", chave, cache_dir=settings.cache_dir)
    if cached is not None:
        return [DestaqueSugerido.model_validate(item) for item in cached]

    from src.llm import client

    try:
        resposta = client.run_structured(
            "plano_destaques", entrada, PlanoDestaques, settings=settings
        )
    except client.LLMError as exc:
        log.warning("Planejamento de destaques sem LLM (%s): nenhuma frase selecionada.", exc)
        return []
    write_json_cache(
        "llm_destaques",
        chave,
        [item.model_dump() for item in resposta.destaques],
        settings.cache_dir,
    )
    return resposta.destaques


def validate_highlights(
    sugestoes: Sequence[DestaqueSugerido],
    frases: Sequence[FraseCandidata],
    params: DestaqueParams | None = None,
    *,
    palavras: Sequence[PalavraGlobal] | None = None,
    limites_segmentos: Sequence[tuple[float, float]] | None = None,
) -> list[ItemDestaque]:
    """Aceita até cinco palavras consecutivas dentro de uma frase, sem reescrita."""
    params = params or DestaqueParams()
    por_indice = {p.indice: p for p in palavras} if palavras is not None else None
    resultado: list[ItemDestaque] = []
    for bruto in sorted(sugestoes, key=lambda s: (s.trecho_inicio_palavra, s.trecho_fim_palavra)):
        inicio_indice, fim_indice = bruto.trecho_inicio_palavra, bruto.trecho_fim_palavra
        quantidade = fim_indice - inicio_indice + 1
        if not 1 <= quantidade <= MAX_PALAVRAS_DESTAQUE:
            continue
        frase = next(
            (
                f
                for f in frases
                if f.trecho_inicio_palavra <= inicio_indice <= fim_indice <= f.trecho_fim_palavra
            ),
            None,
        )
        if frase is None:
            continue
        if por_indice is None:
            if (inicio_indice, fim_indice) != (
                frase.trecho_inicio_palavra,
                frase.trecho_fim_palavra,
            ):
                continue
            inicio, fim, texto = frase.inicio, frase.fim, frase.texto
        else:
            trecho = [por_indice.get(i) for i in range(inicio_indice, fim_indice + 1)]
            if any(
                p is None or p.clipe != frase.clipe or p.segmento != frase.segmento
                for p in trecho
            ):
                continue
            selecionadas = [p for p in trecho if p is not None]
            inicio = selecionadas[0].palavra.inicio
            fim = selecionadas[-1].palavra.fim
            texto = " ".join(p.palavra.texto for p in selecionadas)
        if (
            " ".join(bruto.texto.split()) != texto
            or len(texto.split()) > MAX_PALAVRAS_DESTAQUE
            or not params.duracao_min <= fim - inicio <= params.duracao_max
        ):
            continue
        if limites_segmentos is not None:
            if not 0 <= frase.segmento < len(limites_segmentos):
                continue
            limite_inicio, limite_fim = limites_segmentos[frase.segmento]
            if (
                inicio < limite_inicio - 1e-6
                or fim + params.duracao_permanencia > limite_fim + 1e-6
            ):
                continue
        if resultado and (
            inicio - resultado[-1].inicio < params.intervalo_min
            or inicio < resultado[-1].fim + params.duracao_permanencia
            or inicio_indice <= resultado[-1].trecho_fim_palavra + 1
        ):
            continue
        resultado.append(
            ItemDestaque(
                id=len(resultado),
                clipe=frase.clipe,
                segmento=frase.segmento,
                trecho_inicio_palavra=inicio_indice,
                trecho_fim_palavra=fim_indice,
                inicio=inicio,
                fim=fim,
                texto=texto,
                motivo=bruto.motivo.strip(),
            )
        )
    return resultado


def plan_highlights(
    palavras: Sequence[PalavraGlobal],
    plano: PlanoImagens,
    params: DestaqueParams | None = None,
    settings: Settings | None = None,
    *,
    limites_segmentos: Sequence[tuple[float, float]] | None = None,
) -> PlanoImagens:
    """Acrescenta ao plano sem alterar imagens, zooms ou cutaways."""
    params = params or DestaqueParams()
    frases = segmentar_frases(palavras, params)
    if limites_segmentos is not None:
        # `visible_words` pode remapear uma palavra que cruza um corte interno.
        # Uma frase contendo esse resto atravessaria a emenda no .ass.
        frases = [
            frase
            for frase in frases
            if 0 <= frase.segmento < len(limites_segmentos)
            and frase.inicio >= limites_segmentos[frase.segmento][0] - 1e-6
            and frase.fim <= limites_segmentos[frase.segmento][1] + 1e-6
        ]
    sugestoes = suggest_highlights(frases, params, settings, palavras=palavras)
    atualizado = plano.model_copy(deep=True)
    atualizado.destaques = validate_highlights(
        sugestoes, frases, params, palavras=palavras, limites_segmentos=limites_segmentos
    )
    atualizado.versao_destaques = PLANO_DESTAQUES_VERSION
    atualizado.duracao_permanencia_destaques = params.duracao_permanencia
    log.info(
        "Plano de destaques: %d frase(s) selecionada(s) de %d.",
        len(atualizado.destaques),
        len(frases),
    )
    return atualizado


def plan_highlights_project(
    project: Project,
    plano: PlanoImagens,
    transcriber: Callable[[Path], object],
    params: DestaqueParams | None = None,
    settings: Settings | None = None,
) -> PlanoImagens:
    """Planeja usando palavras globais após os cortes, com tempos `t_out`."""
    from src.cuts import TimeMap
    from src.images import global_words

    segmentos = TimeMap(project.timeline).segments
    limites = [(segmento.out_inicio, segmento.out_fim) for segmento in segmentos]
    return plan_highlights(
        global_words(project, transcriber),
        plano,
        params,
        settings,
        limites_segmentos=limites,
    )
