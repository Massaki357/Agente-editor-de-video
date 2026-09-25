"""Troca um elemento do plano por busca, alternativa salva ou upload."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from src.broll.planner import VideoBroll
from src.broll.source import (
    VideoCandidate,
    prepare_broll,
    prepare_candidate,
    prepare_uploaded_broll,
)
from src.cache import file_hash
from src.images import Candidato, PlanoImagens, search_images

ModoTroca = Literal["busca", "alternativa", "upload"]


def replace_element(
    plano: PlanoImagens,
    element_id: str,
    modo: ModoTroca,
    *,
    query: str | None = None,
    indice: int | None = None,
    upload: Path | None = None,
    pid: str | None = None,
) -> PlanoImagens:
    """Devolve novo plano; nenhum item original muda se a preparação falhar."""
    updated = plano.model_copy(deep=True)
    if element_id.startswith("img_"):
        item = next((i for i in updated.itens if f"img_{i.id:03d}" == element_id), None)
        if item is None:
            raise KeyError(element_id)
        if modo == "busca":
            new_query = (query or "").strip()
            if not 2 <= len(new_query) <= 80:
                raise ValueError("a busca de imagem precisa ter entre 2 e 80 caracteres")
            candidatos = search_images(new_query)
            if not candidatos:
                raise ValueError(f"nenhuma foto encontrada para '{new_query}'")
            item.query, item.candidatos, item.escolhida = new_query, candidatos, 0
        elif modo == "alternativa":
            if indice is None or not 0 <= indice < len(item.candidatos):
                raise ValueError("índice de foto alternativa inválido")
            item.escolhida = indice
        elif modo == "upload":
            if upload is None or not upload.is_file() or pid is None:
                raise ValueError("imagem enviada indisponível")
            from PIL import Image

            with Image.open(upload) as picture:
                picture.verify()
            ident = file_hash(upload)[:16]
            item.candidatos.append(Candidato(
                fonte="upload", id=ident, url=str(upload),
                miniatura=f"/api/projects/{pid}/replace/{element_id}/media?v={ident}",
                autor=upload.name, pagina="",
            ))
            item.escolhida = len(item.candidatos) - 1
        else:
            raise ValueError(f"modo de troca inválido: {modo}")
        item.ativa = True
        return updated

    if element_id.startswith("broll_"):
        item = next((i for i in updated.broll if f"broll_{i.id:03d}" == element_id), None)
        if item is None:
            raise KeyError(element_id)
        if modo == "busca":
            new_query = (query or "").strip()
            if not 2 <= len(new_query) <= 80:
                raise ValueError("a busca de B-roll precisa ter entre 2 e 80 caracteres")
            prepared = prepare_broll(new_query, item.duracao_max)
            if prepared is None:
                raise ValueError(f"nenhum vídeo encontrado para '{new_query}'")
            item.query = new_query
            item.alternativas = [c.model_dump(mode="json") for c in prepared.alternativas]
        elif modo == "alternativa":
            if indice is None or not 0 <= indice < len(item.alternativas):
                raise ValueError("índice de vídeo alternativo inválido")
            candidate = VideoCandidate.model_validate(item.alternativas[indice])
            prepared = prepare_candidate(candidate, item.query, item.duracao_max)
        elif modo == "upload":
            if upload is None or not upload.is_file():
                raise ValueError("vídeo enviado indisponível")
            prepared = prepare_uploaded_broll(upload, item.duracao_max)
        else:
            raise ValueError(f"modo de troca inválido: {modo}")
        item.video = VideoBroll(
            arquivo=prepared.arquivo, fonte=prepared.fonte, id=prepared.id,
            pagina=prepared.pagina, autor=prepared.autor,
        )
        item.ativo = item.aprovado = True
        return updated

    raise KeyError(element_id)
