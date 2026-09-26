"""Registro de presets de legenda contínua (Caption Style Engine, fundação).

Um preset dá um nome único a uma combinação de `CaptionStyle` (visual, já existente
em `src/captions.py`) e `animacao` (por enquanto só `"none"` — a Etapa 4 do
`PLANO_CAPTIONS_TRANSITIONS_CLAUDE_CODE.md` liga outros valores a variações reais de
revelação). `group_words()`/`build_ass()` continuam recebendo só o `CaptionStyle` e não
conhecem o conceito de preset: o registro fica inteiramente fora da lógica de
sincronização (agrupamento de palavras, tempos em t_out).

Cobre só a legenda contínua. A legenda de destaque (`src/highlight_captions/`) tem seu
próprio `HighlightStyle` independente; unificar os dois sistemas é uma decisão em
aberto (ver `docs/video-engine-architecture.md`, §4 e §12) e não faz parte desta etapa.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from src.captions import CaptionStyle

log = logging.getLogger(__name__)

# Só "none" existe até a Etapa 4 implementar variações reais de revelação/movimento.
Animacao = Literal["none"]

PRESET_PADRAO = "default"


class CaptionPreset(BaseModel):
    """Nome + estilo visual + animação de um preset de legenda contínua."""

    nome: str
    style: CaptionStyle = Field(default_factory=CaptionStyle)
    animacao: Animacao = "none"


# O preset "default" é literalmente `CaptionStyle()`: o visual que já existia antes
# desta etapa, para nenhum vídeo existente mudar de aparência sem pedir um preset novo.
_REGISTRY: dict[str, CaptionPreset] = {
    PRESET_PADRAO: CaptionPreset(nome=PRESET_PADRAO, style=CaptionStyle()),
}


def presets_disponiveis() -> list[str]:
    """Nomes registrados, em ordem alfabética."""
    return sorted(_REGISTRY)


def resolve_preset(nome: str | None) -> CaptionPreset:
    """Preset pelo nome; nome ausente ou desconhecido cai no `default` (com aviso)."""
    if nome is None:
        return _REGISTRY[PRESET_PADRAO]
    preset = _REGISTRY.get(nome)
    if preset is None:
        log.warning("Preset de legenda '%s' não encontrado; usando '%s'.", nome, PRESET_PADRAO)
        return _REGISTRY[PRESET_PADRAO]
    return preset


def resolved_style(preset_nome: str | None, override: CaptionStyle | None) -> CaptionStyle:
    """Estilo efetivo: `override` explícito quando informado, senão o do preset."""
    return override if override is not None else resolve_preset(preset_nome).style
