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
    descricao: str = ""
    style: CaptionStyle = Field(default_factory=CaptionStyle)
    animacao: Animacao = "none"


# O preset "default" é literalmente `CaptionStyle()`: o visual que já existia antes
# desta etapa, para nenhum vídeo existente mudar de aparência sem pedir um preset novo.
_REGISTRY: dict[str, CaptionPreset] = {
    PRESET_PADRAO: CaptionPreset(
        nome=PRESET_PADRAO,
        descricao="Visual legado do editor, preservado para compatibilidade.",
        style=CaptionStyle(),
    ),
    "clean": CaptionPreset(
        nome="clean",
        descricao="Texto leve e natural no terço inferior.",
        style=CaptionStyle(
            fonte="Poppins Regular",
            tamanho=76,
            negrito=False,
            cor="#FFFFFF",
            cor_destaque="#76E4F7",
            contorno=4,
            sombra=2,
            margem_inferior=430,
            margem_lateral=90,
            maiusculas=False,
            palavras_max=5,
            destaque_escala=106,
        ),
    ),
    "bold": CaptionPreset(
        nome="bold",
        descricao="Tipografia grande, forte e de alto contraste.",
        style=CaptionStyle(
            tamanho=100,
            cor="#FFFFFF",
            cor_destaque="#FFD400",
            contorno=9,
            sombra=4,
            margem_inferior=500,
            margem_lateral=72,
            maiusculas=True,
            palavras_max=3,
            destaque_escala=116,
        ),
    ),
    "minimal": CaptionPreset(
        nome="minimal",
        descricao="Legenda discreta, compacta e com pouco contorno.",
        style=CaptionStyle(
            fonte="Poppins Regular",
            tamanho=62,
            negrito=False,
            cor="#F7F7F7",
            cor_destaque="#B8F2E6",
            contorno=2,
            sombra=1,
            margem_inferior=350,
            margem_lateral=120,
            maiusculas=False,
            palavras_max=6,
            destaque_escala=104,
        ),
    ),
    "cinematic": CaptionPreset(
        nome="cinematic",
        descricao="Serifa clara com paleta quente e posição superior.",
        style=CaptionStyle(
            fonte="Roboto Slab",
            tamanho=70,
            negrito=False,
            cor="#F8F0DF",
            cor_destaque="#E6B566",
            cor_contorno="#17130E",
            contorno=3,
            sombra=4,
            alinhamento="superior",
            margem_inferior=150,
            margem_lateral=110,
            maiusculas=False,
            palavras_max=5,
            destaque_escala=108,
        ),
    ),
    "social": CaptionPreset(
        nome="social",
        descricao="Bloco central enérgico para vídeos curtos.",
        style=CaptionStyle(
            tamanho=96,
            cor="#FFFFFF",
            cor_destaque="#00E5FF",
            cor_contorno="#111111",
            contorno=6,
            sombra=0,
            fundo=True,
            alinhamento="centro",
            margem_lateral=80,
            maiusculas=True,
            palavras_max=2,
            pausa_quebra=0.35,
            destaque_escala=115,
        ),
    ),
    "karaoke": CaptionPreset(
        nome="karaoke",
        descricao="Frases um pouco mais longas com destaque forte por palavra.",
        style=CaptionStyle(
            tamanho=84,
            cor="#FFFFFF",
            cor_destaque="#7CFF6B",
            cor_contorno="#071A05",
            contorno=7,
            sombra=3,
            margem_inferior=300,
            margem_lateral=80,
            maiusculas=False,
            palavras_max=7,
            pausa_quebra=0.6,
            destaque_escala=114,
        ),
    ),
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
