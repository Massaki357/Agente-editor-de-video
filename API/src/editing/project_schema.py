"""Elementos identificáveis do projeto, no mesmo project.json da timeline.

`dados` conserva o schema completo do planejador de origem. Os campos comuns
permitem localizar e invalidar elementos sem conhecer cada formato interno.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from src.images import PlanoImagens
    from src.project import Timeline
    from src.reframe import CameraPath

TipoElemento = Literal[
    "clipe", "corte", "crop", "imagem", "zoom", "broll", "legenda", "destaque"
]
TIPOS_PLANO = {"imagem", "zoom", "broll", "destaque"}


class Elemento(BaseModel):
    """Uma decisão localizável na timeline; tempos são sempre t_out."""

    model_config = ConfigDict(allow_inf_nan=False)

    id: str = Field(pattern=r"^(clip|cut|crop|img|zoom|broll|caption|highlight)_[A-Za-z0-9_]+$")
    tipo: TipoElemento
    inicio: float = Field(ge=0)
    fim: float = Field(ge=0)
    clipe: int | None = Field(default=None, ge=0)
    ativo: bool = True
    obsoleto: bool = False
    origem: Literal["timeline", "timeline_legada", "plano", "render"] = "plano"
    dados: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _intervalo(self) -> Elemento:
        if self.fim <= self.inicio:
            raise ValueError(f"{self.id}: fim precisa ser maior que início")
        return self


class DocumentoEdicao(BaseModel):
    """Decisões que serão editadas por ID e usadas pelo render."""

    versao: Literal[2] = 2
    assinatura_timeline: str | None = None
    assinatura_plano: str | None = None
    metadados_plano: dict[str, Any] = Field(default_factory=dict)
    elementos: list[Elemento] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ids_unicos(self) -> DocumentoEdicao:
        ids = [e.id for e in self.elementos]
        if len(ids) != len(set(ids)):
            raise ValueError("ids de elementos duplicados no documento de edição")
        return self

    def por_id(self, id_elemento: str) -> Elemento | None:
        return next((e for e in self.elementos if e.id == id_elemento), None)


def _cut_id(clip_id: str, inicio_src: float, fim_src: float) -> str:
    chave = f"{clip_id}:{inicio_src:.6f}:{fim_src:.6f}".encode()
    return f"cut_{hashlib.sha256(chave).hexdigest()[:12]}"


def assinatura_timeline(timeline: Timeline) -> str:
    """Identifica a ordem e os trechos, inclusive quando a duração total não muda."""
    dados = [(clip.id, clip.trechos) for clip in timeline.clipes]
    return hashlib.sha256(json.dumps(dados).encode()).hexdigest()[:16]


def marcar_obsoletos(documento: DocumentoEdicao, timeline: Timeline) -> DocumentoEdicao:
    atual = assinatura_timeline(timeline)
    if documento.assinatura_timeline is not None and documento.assinatura_timeline != atual:
        for elemento in documento.elementos:
            if elemento.origem in {"plano", "render"}:
                elemento.obsoleto = True
    documento.assinatura_timeline = atual
    return documento


def corrigir_intervalo_destaques(documento: DocumentoEdicao) -> DocumentoEdicao:
    """Normaliza documentos v2 iniciais que guardavam só o fim da fala."""
    if documento.assinatura_timeline is not None:
        return documento
    for elemento in documento.elementos:
        if elemento.tipo != "destaque" or elemento.origem != "plano":
            continue
        fim_fala = elemento.dados.get("fim")
        if fim_fala is None or abs(elemento.fim - fim_fala) > 1e-6:
            continue
        permanencia = elemento.dados.get("duracao_permanencia")
        if permanencia is None:
            permanencia = documento.metadados_plano.get("duracao_permanencia_destaques")
        if permanencia is None:
            permanencia = 1.2
        elemento.fim += permanencia
    return documento


def normalizar_origens(documento: DocumentoEdicao) -> DocumentoEdicao:
    """Classifica elementos v2 iniciais, gravados antes do campo `origem`."""
    for elemento in documento.elementos:
        if elemento.tipo in {"clipe", "corte"}:
            elemento.origem = "timeline"
        elif elemento.tipo == "crop" or elemento.id.startswith("caption_"):
            elemento.origem = (
                "timeline_legada" if elemento.id.startswith("caption_legacy_") else "render"
            )
        elif elemento.id.startswith(("img_legacy_", "zoom_legacy_")):
            elemento.origem = "timeline_legada"
    return documento


def sincronizar_timeline(documento: DocumentoEdicao, timeline: Timeline) -> DocumentoEdicao:
    """Atualiza clipes/cortes mantendo IDs dos demais elementos."""
    outros = [
        e for e in documento.elementos if e.origem not in {"timeline", "timeline_legada"}
    ]
    base: list[Elemento] = []
    for i, clip in enumerate(timeline.clipes):
        if clip.duracao_mantida <= 0:
            continue
        base.append(
            Elemento(
                id=clip.id,
                tipo="clipe",
                inicio=clip.offset,
                fim=clip.offset + clip.duracao_mantida,
                clipe=i,
                origem="timeline",
                dados={"arquivo": clip.arquivo},
            )
        )
        t_out = clip.offset
        for inicio_src, fim_src in clip.trechos:
            duracao = fim_src - inicio_src
            base.append(
                Elemento(
                    id=_cut_id(clip.id, inicio_src, fim_src),
                    tipo="corte",
                    inicio=t_out,
                    fim=t_out + duracao,
                    clipe=i,
                    origem="timeline",
                    dados={"inicio_src": inicio_src, "fim_src": fim_src},
                )
            )
            t_out += duracao
    for tipo, prefixo, itens in (
        ("imagem", "img_legacy", timeline.imagens),
        ("zoom", "zoom_legacy", timeline.zooms),
    ):
        for indice, item in enumerate(itens):
            dados = item.model_dump(mode="json")
            sufixo = hashlib.sha256(json.dumps(dados, sort_keys=True).encode()).hexdigest()[:12]
            base.append(
                Elemento(
                    id=f"{prefixo}_{sufixo}_{indice}",
                    tipo=tipo,
                    inicio=item.inicio,
                    fim=item.inicio + item.duracao,
                    obsoleto=True,
                    origem="timeline_legada",
                    dados=dados,
                )
            )
    if timeline.legendas and timeline.duracao_total > 0:
        sufixo = hashlib.sha256(timeline.legendas.encode()).hexdigest()[:12]
        base.append(
            Elemento(
                id=f"caption_legacy_{sufixo}",
                tipo="legenda",
                inicio=0,
                fim=timeline.duracao_total,
                obsoleto=True,
                origem="timeline_legada",
                dados={"arquivo": timeline.legendas},
            )
        )
    return DocumentoEdicao.model_validate(
        {**documento.model_dump(), "elementos": [*base, *outros]}
    )


def com_plano(documento: DocumentoEdicao, plano: PlanoImagens) -> DocumentoEdicao:
    """Substitui decisões criativas mantendo os IDs dos outros tipos."""
    outros = [
        e for e in documento.elementos if not (e.tipo in TIPOS_PLANO and e.origem == "plano")
    ]
    novos: list[Elemento] = []
    for tipo, prefixo, itens in (
        ("imagem", "img", plano.itens),
        ("zoom", "zoom", plano.zooms),
        ("broll", "broll", plano.broll),
        ("destaque", "highlight", plano.destaques),
    ):
        for item in itens:
            inicio = item.inicio
            fim = item.fim
            if tipo == "destaque":
                permanencia = item.duracao_permanencia
                if permanencia is None:
                    permanencia = plano.duracao_permanencia_destaques
                if permanencia is None:
                    permanencia = 1.2
                fim += permanencia
            novos.append(
                Elemento(
                    id=f"{prefixo}_{item.id:03d}",
                    tipo=tipo,
                    inicio=inicio,
                    fim=fim,
                    clipe=item.clipe,
                    ativo=getattr(item, "ativa", getattr(item, "ativo", True)),
                    dados=item.model_dump(mode="json"),
                )
            )
    metadados = plano.model_dump(
        mode="json", exclude={"assinatura", "itens", "zooms", "broll", "destaques"}
    )
    return DocumentoEdicao(
        assinatura_timeline=documento.assinatura_timeline,
        assinatura_plano=plano.assinatura,
        metadados_plano=metadados,
        elementos=[*outros, *novos],
    )


def plano_do_documento(documento: DocumentoEdicao) -> PlanoImagens | None:
    """Reconstrói o plano exclusivamente dos elementos do project.json."""
    if documento.assinatura_plano is None:
        return None
    from src.images import PlanoImagens

    grupos = {"imagem": "itens", "zoom": "zooms", "broll": "broll", "destaque": "destaques"}
    payload: dict[str, Any] = {
        **documento.metadados_plano,
        "assinatura": documento.assinatura_plano,
    }
    for destino in grupos.values():
        payload[destino] = []
    for elemento in documento.elementos:
        if elemento.tipo in grupos and elemento.origem == "plano":
            dados = elemento.dados.copy()
            inicio_original = dados["inicio"]
            if elemento.tipo in {"imagem", "zoom"}:
                fim_original = inicio_original + dados["duracao"]
            elif elemento.tipo == "broll":
                fim_original = inicio_original + dados["duracao_max"]
            else:
                fim_original = dados["fim"]
            dados["inicio"] = elemento.inicio
            dados["clipe"] = elemento.clipe
            dados["ativo" if elemento.tipo != "imagem" else "ativa"] = elemento.ativo
            intervalo_editado = (
                abs(elemento.inicio - inicio_original) > 1e-8
                or abs(elemento.fim - fim_original) > 1e-8
            )
            if elemento.tipo in {"imagem", "zoom"}:
                if intervalo_editado:
                    dados["duracao"] = round(elemento.fim - elemento.inicio, 9)
            elif elemento.tipo == "broll":
                if intervalo_editado:
                    dados["duracao_max"] = round(elemento.fim - elemento.inicio, 9)
            else:
                fim_fala = dados["fim"]
                if elemento.fim < fim_fala:
                    raise ValueError(f"{elemento.id}: fim visual antes do fim da fala")
                base = dados.get("duracao_permanencia")
                if base is None:
                    base = documento.metadados_plano.get("duracao_permanencia_destaques")
                if base is None:
                    base = 1.2
                permanencia = elemento.fim - fim_fala
                if abs(permanencia - base) > 1e-6:
                    dados["duracao_permanencia"] = permanencia
            payload[grupos[elemento.tipo]].append(dados)
    return PlanoImagens.model_validate(payload)


def com_crops(documento: DocumentoEdicao, crops: list[Elemento]) -> DocumentoEdicao:
    outros = [e for e in documento.elementos if e.tipo != "crop"]
    return DocumentoEdicao.model_validate(
        {**documento.model_dump(), "elementos": [*outros, *crops]}
    )


def com_legendas(documento: DocumentoEdicao, legendas: list[Elemento]) -> DocumentoEdicao:
    outros = [e for e in documento.elementos if e.tipo != "legenda"]
    return DocumentoEdicao.model_validate(
        {**documento.model_dump(), "elementos": [*outros, *legendas]}
    )


def keyframes_crop(timeline: Timeline, cameras: dict[int, CameraPath]) -> list[Elemento]:
    """Resume a janela real da câmera em keyframes por trecho mantido."""
    resultado: list[Elemento] = []
    for i, clip in enumerate(timeline.clipes):
        camera = cameras.get(i)
        if camera is None:
            continue
        t_out = clip.offset
        for inicio_src, fim_src in clip.trechos:
            duracao = fim_src - inicio_src
            n = max(1, int(duracao / 0.5 + 0.999999))
            for k in range(n):
                a_src = inicio_src + duracao * k / n
                b_src = inicio_src + duracao * (k + 1) / n
                x, y, w, h = camera.window_f(a_src)
                resultado.append(
                    Elemento(
                        id=f"crop_{clip.id[5:]}_{round(a_src * 1000):09d}",
                        tipo="crop",
                        inicio=t_out + a_src - inicio_src,
                        fim=t_out + b_src - inicio_src,
                        clipe=i,
                        origem="render",
                        dados={
                            "inicio_src": a_src,
                            "fim_src": b_src,
                            "x": x,
                            "y": y,
                            "largura": w,
                            "altura": h,
                        },
                    )
                )
            t_out += duracao
    return resultado


_TAG_ASS = re.compile(r"\{[^}]*\}")


def _tempo_ass(valor: str) -> float:
    horas, minutos, segundos = valor.split(":")
    return int(horas) * 3600 + int(minutos) * 60 + float(segundos)


def eventos_ass(path: Path | None) -> list[Elemento]:
    """Registra cada evento visível da legenda contínua com seu tempo e texto."""
    if path is None:
        return []
    resultado: list[Elemento] = []
    for linha in path.read_text(encoding="utf-8-sig").splitlines():
        if not linha.startswith("Dialogue:"):
            continue
        campos = linha.removeprefix("Dialogue:").strip().split(",", 9)
        if len(campos) != 10:
            continue
        inicio, fim = _tempo_ass(campos[1]), _tempo_ass(campos[2])
        texto = _TAG_ASS.sub("", campos[9]).replace(r"\N", " ").strip()
        if not texto or fim <= inicio:
            continue
        chave = f"{texto}:{inicio:.2f}:{fim:.2f}".encode()
        sufixo = hashlib.sha256(chave).hexdigest()[:12]
        repetidos = sum(e.id.startswith(f"caption_{sufixo}") for e in resultado)
        resultado.append(
            Elemento(
                id=f"caption_{sufixo}_{repetidos}",
                tipo="legenda",
                inicio=inicio,
                fim=fim,
                origem="render",
                dados={"texto": texto, "evento_ass": campos[9]},
            )
        )
    return resultado
