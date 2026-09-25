"""Ferramentas por ID: o LLM propõe, o código valida e altera uma cópia do plano."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.broll.planner import BrollParams
from src.cuts import TimeMap
from src.editing.preview import list_editor_elements, remove_element
from src.editing.project_schema import com_plano, plano_do_documento
from src.editing.replace import replace_element
from src.highlight_captions.planner import DestaqueParams
from src.images import PlanoImagens, ZoomPlanParams, global_words, timeline_signature
from src.pipeline import PipelineOptions
from src.project import Project


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class remover_elemento(_Args):  # noqa: N801 - nome exposto ao provedor
    """Remover um elemento existente pelo ID, mantendo a possibilidade de desfazer."""

    id: str = Field(description="ID exato do elemento, como zoom_002")


class trocar_imagem(_Args):  # noqa: N801
    """Trocar uma imagem existente por uma nova busca de fotos."""

    id: str = Field(description="ID exato da imagem, como img_003")
    nova_query: str = Field(min_length=2, max_length=80, description="nova busca visual")


class ajustar_duracao(_Args):  # noqa: N801
    """Alterar duração de imagem, zoom ou destaque; B-roll só pode ser encurtado."""

    id: str = Field(description="ID exato do elemento")
    nova_duracao: float = Field(gt=0, le=9, description="duração total desejada em segundos")


class ajustar_intensidade_zoom(_Args):  # noqa: N801
    """Ajustar o pico de um zoom, como fator de escala (1,10 = 10% de aproximação)."""

    id: str = Field(description="ID exato do zoom")
    novo_valor: float = Field(ge=1.02, le=1.6, description="pico desejado da escala")


class mover_elemento(_Args):  # noqa: N801
    """Mover imagem ou zoom existente para outro tempo do mesmo trecho mantido."""

    id: str = Field(description="ID exato da imagem ou zoom")
    novo_inicio: float = Field(ge=0, description="novo início na timeline final, em segundos")


class listar_elementos(_Args):  # noqa: N801
    """Listar IDs, tipos e intervalos dos elementos já existentes no vídeo."""

    filtro: str | None = Field(
        default=None, max_length=80, description="tipo, ID ou trecho do rótulo"
    )


SCHEMAS: tuple[type[_Args], ...] = (
    remover_elemento, trocar_imagem, ajustar_duracao,
    ajustar_intensidade_zoom, mover_elemento, listar_elementos,
)
SCHEMA_BY_NAME = {schema.__name__: schema for schema in SCHEMAS}
VisualType = Literal["imagem", "broll", "zoom", "destaque"]


@dataclass
class WordTiming:
    """Palavra preservada, com tempo final, usada para aparar um B-roll."""

    indice: int
    clipe: int
    inicio: float
    fim: float
    texto: str


@dataclass
class EditSession:
    """Rascunho local; o chamador decide se fará prévia e publicará a versão."""

    project: Project
    plan: PlanoImagens
    options: PipelineOptions
    words: list[WordTiming] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.project = self.project.model_copy(deep=True)
        self.plan = self.plan.model_copy(deep=True)
        if self.plan.assinatura != timeline_signature(self.project):
            raise ValueError("o plano não corresponde à timeline atual")
        if plano_do_documento(self.project.documento) != self.plan:
            raise ValueError("o plano não corresponde ao documento do projeto")

    @classmethod
    def with_transcription(
        cls, project: Project, plan: PlanoImagens, options: PipelineOptions,
        transcriber: Callable[[Path], object],
    ) -> EditSession:
        """Prepara tempos finais das palavras no worker antes de editar B-roll."""
        words = [WordTiming(
            indice=value.indice, clipe=value.clipe,
            inicio=value.palavra.inicio, fim=value.palavra.fim,
            texto=value.palavra.texto,
        ) for value in global_words(project, transcriber)]
        return cls(project, plan, options, words=words)


def _find(plan: PlanoImagens, element_id: str) -> tuple[VisualType, Any]:
    for prefix, kind, items in (
        ("img", "imagem", plan.itens), ("broll", "broll", plan.broll),
        ("zoom", "zoom", plan.zooms), ("highlight", "destaque", plan.destaques),
    ):
        if element_id.startswith(f"{prefix}_"):
            item = next((i for i in items if f"{prefix}_{i.id:03d}" == element_id), None)
            if item is None:
                break
            return kind, item
    raise ValueError(f"elemento {element_id} não existe no plano")


def _active(item: Any) -> bool:
    return bool(getattr(item, "ativa", getattr(item, "ativo", False)))


def _interval(item: Any, kind: VisualType, plan: PlanoImagens) -> tuple[float, float]:
    end = item.fim
    if kind == "destaque":
        permanence = item.duracao_permanencia
        if permanence is None:
            permanence = plan.duracao_permanencia_destaques
        end += permanence if permanence is not None else 1.2
    return item.inicio, end


def _validate(session: EditSession, plan: PlanoImagens, element_id: str) -> None:
    kind, item = _find(plan, element_id)
    if not _active(item):
        return
    options = session.options
    image_rules = options.parametros_imagens
    zoom_rules = ZoomPlanParams()
    broll_rules = BrollParams(intervalo_min=options.broll_intervalo_min)
    highlight_rules = DestaqueParams(
        duracao_permanencia=options.estilo_destaque.duracao_permanencia
    )
    start, end = _interval(item, kind, plan)
    duration = item.fim - item.inicio
    if not all(math.isfinite(value) for value in (start, end, duration)):
        raise ValueError("tempo inválido")
    tm = TimeMap(session.project.timeline)
    if not any(
        segment.clip == item.clipe
        and start >= segment.out_inicio - 1e-6
        and end <= segment.out_fim + 1e-6
        for segment in tm.segments
    ):
        raise ValueError("o elemento deve caber inteiro no mesmo trecho mantido")
    bounds = {
        "imagem": (image_rules.duracao_min, image_rules.duracao_max),
        "zoom": (zoom_rules.duracao_min, zoom_rules.duracao_max),
        "broll": (broll_rules.duracao_min, broll_rules.duracao_max),
        "destaque": (highlight_rules.duracao_min, highlight_rules.duracao_max),
    }
    minimum, maximum = bounds[kind]
    if not minimum - 1e-6 <= duration <= maximum + 1e-6:
        raise ValueError(f"duração de {kind} deve ficar entre {minimum:g} e {maximum:g} s")
    if kind == "destaque" and len(item.texto.split()) > 5:
        raise ValueError("destaque deve ter no máximo cinco palavras")
    if kind == "zoom" and item.intensidade is not None:
        if item.intensidade > options.parametros_zoom.escala + 1e-9:
            raise ValueError("intensidade excede o limite de zoom da geração")
    if kind == "broll" and (not item.aprovado or item.video is None):
        raise ValueError("B-roll ainda não foi aprovado no vídeo")

    peers: list[Any] = {
        "imagem": plan.itens, "zoom": plan.zooms,
        "broll": plan.broll, "destaque": plan.destaques,
    }[kind]
    spacing = {
        "imagem": image_rules.intervalo_min, "zoom": zoom_rules.intervalo_min,
        "broll": broll_rules.intervalo_min, "destaque": highlight_rules.intervalo_min,
    }[kind]
    for other in peers:
        if other.id == item.id or not _active(other):
            continue
        other_start, other_end = _interval(other, kind, plan)
        if abs(start - other_start) < spacing - 1e-6 or start < other_end and other_start < end:
            raise ValueError(f"{kind} conflita com outro elemento ou excede a densidade")
        if kind == "broll" and (
            start >= other_start and start < other_end + broll_rules.retorno_min
            or other_start >= start and other_start < end + broll_rules.retorno_min
        ):
            raise ValueError("B-roll precisa retornar à câmera entre cutaways")
    if kind in {"imagem", "broll"}:
        others = plan.broll if kind == "imagem" else plan.itens
        other_kind: VisualType = "broll" if kind == "imagem" else "imagem"
        for other in others:
            effect_enabled = options.broll if other_kind == "broll" else options.imagens
            if effect_enabled and _active(other) and (
                other_kind != "broll" or other.aprovado and other.video
            ):
                other_start, other_end = _interval(other, other_kind, plan)
                if start < other_end and other_start < end:
                    raise ValueError("imagem e B-roll não podem se sobrepor")
    if kind == "zoom" and options.broll:
        for other in plan.broll:
            if _active(other) and other.aprovado and other.video:
                other_start, other_end = _interval(other, "broll", plan)
                if start < other_end and other_start < end:
                    raise ValueError("zoom ficaria oculto pelo B-roll")


def execute_tool(session: EditSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Valida argumentos e plano antes de substituir o rascunho da sessão."""
    schema = SCHEMA_BY_NAME.get(name)
    if schema is None:
        raise ValueError(f"ferramenta desconhecida: {name}")
    args = schema.model_validate(arguments)
    if name == "listar_elementos":
        entries = list_editor_elements(session.project, session.options.model_dump()).elements
        term = (args.filtro or "").strip().casefold()
        return {
            "elementos": [entry.model_dump() for entry in entries if not term or term in (
                entry.id + " " + entry.tipo + " " + entry.rotulo
            ).casefold()]
        }
    kind, item = _find(session.plan, args.id)
    enabled = {
        "imagem": session.options.imagens,
        "broll": session.options.broll and session.options.reenquadrar,
        "zoom": session.options.zooms and session.options.reenquadrar,
        "destaque": session.options.legendas_destaque,
    }
    if not enabled[kind]:
        raise ValueError(f"{kind} não estava ligado no último vídeo")
    if name == "remover_elemento":
        updated = remove_element(session.plan, args.id)
    elif name == "trocar_imagem":
        if kind != "imagem":
            raise ValueError("trocar_imagem aceita apenas IDs de imagem")
        updated = replace_element(session.plan, args.id, "busca", query=args.nova_query)
    else:
        if not _active(item):
            raise ValueError("elemento está removido")
        updated = session.plan.model_copy(deep=True)
        _, selected = _find(updated, args.id)
        if name == "ajustar_duracao":
            if kind == "destaque":
                permanence = args.nova_duracao - (selected.fim - selected.inicio)
                if not 0 <= permanence <= 5:
                    raise ValueError("permanência de destaque deve ficar entre 0 e 5 s")
                selected.duracao_permanencia = permanence
            elif kind == "broll":
                target_end = selected.inicio + args.nova_duracao
                if target_end > selected.fim + 1e-6:
                    raise ValueError("B-roll não pode passar do fim da frase original")
                relevant = {
                    word.indice: word for word in session.words
                    if word.clipe == selected.clipe and
                    selected.trecho_inicio_palavra <= word.indice <= selected.trecho_fim_palavra
                }
                if not relevant:
                    raise ValueError("transcrição necessária para ajustar o B-roll")
                matched = next((
                    word for word in relevant.values()
                    if abs(target_end - word.fim) <= 1 / 30 + 1e-6
                ), None)
                if matched is None:
                    raise ValueError("B-roll deve terminar no fim de uma palavra da frase")
                indices = range(selected.trecho_inicio_palavra, matched.indice + 1)
                if any(index not in relevant for index in indices):
                    raise ValueError("transcrição incompleta para ajustar o B-roll")
                selected.duracao_max = matched.fim - selected.inicio
                selected.trecho_fim_palavra = matched.indice
                selected.texto = " ".join(relevant[index].texto for index in indices)
            else:
                selected.duracao = args.nova_duracao
        elif name == "ajustar_intensidade_zoom":
            if kind != "zoom":
                raise ValueError("intensidade só se aplica a zoom")
            selected.intensidade = args.novo_valor
        elif name == "mover_elemento":
            if kind not in {"imagem", "zoom"}:
                raise ValueError("B-roll e destaque seguem as palavras; mover exige replanejamento")
            selected.inicio = args.novo_inicio
        else:
            raise ValueError(f"ferramenta desconhecida: {name}")
    if updated == session.plan:
        raise ValueError("a ação não alterou o elemento")
    _validate(session, updated, args.id)
    session.plan = updated
    session.project.documento = com_plano(session.project.documento, updated)
    change = {"ferramenta": name, "id": args.id, "tipo": kind}
    session.changes.append(change)
    return {"alteracao": change, "elemento": session.project.documento.por_id(args.id).model_dump()}
