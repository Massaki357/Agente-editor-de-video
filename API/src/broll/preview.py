"""Prévia e decisões do usuário sobre cutaways, sem repetir o planejamento do LLM."""

from __future__ import annotations

from src.broll.planner import TransitionOverride, VideoBroll
from src.broll.source import prepare_item
from src.config import Settings, get_settings
from src.images import PlanoImagens


def prepare_preview(plano: PlanoImagens, settings: Settings | None = None) -> PlanoImagens:
    """Anexa clipes aos itens ativos; ausência de resultado deixa o item sem vídeo."""
    settings = settings or get_settings()
    updated = plano.model_copy(deep=True)
    for item in updated.broll:
        if not item.ativo:
            continue
        prepared = prepare_item(item, settings=settings)
        if prepared is None:
            item.video = None
            item.aprovado = False
            continue
        item.video = VideoBroll(
            arquivo=prepared.arquivo,
            fonte=prepared.fonte,
            id=prepared.id,
            pagina=prepared.pagina,
            autor=prepared.autor,
        )
        if prepared.alternativas:
            item.alternativas = [c.model_dump(mode="json") for c in prepared.alternativas]
    return updated


def edit_preview(
    plano: PlanoImagens,
    item_id: int,
    *,
    query: str | None = None,
    ativo: bool | None = None,
    aprovado: bool | None = None,
    transicoes: dict[str, TransitionOverride | None] | None = None,
) -> PlanoImagens:
    """Troca a busca, aprova ou remove; mantém os outros itens e a assinatura."""
    updated = plano.model_copy(deep=True)
    item = next((candidate for candidate in updated.broll if candidate.id == item_id), None)
    if item is None:
        raise KeyError(item_id)
    if query is not None:
        query = query.strip()
        if len(query) < 2 or len(query) > 80:
            raise ValueError("a busca de B-roll precisa ter entre 2 e 80 caracteres")
        if query != item.query:
            item.query = query
            item.video = None
            item.aprovado = False
            item.alternativas = []
    if ativo is not None:
        item.ativo = ativo
        if not ativo:
            item.aprovado = False
    if aprovado is not None:
        if aprovado and (not item.ativo or item.video is None or not item.video.arquivo.is_file()):
            raise ValueError("gere a prévia do B-roll antes de aprovar este item")
        item.aprovado = aprovado
    for key, value in (transicoes or {}).items():
        if key not in {"transicao_entrada", "transicao_saida"}:
            raise ValueError("borda de transição inválida")
        setattr(item, key, value)
    return updated
