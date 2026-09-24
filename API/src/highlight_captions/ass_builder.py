"""Gera .ass com frase de impacto revelada palavra a palavra e permanência."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from src.captions import (
    _cor_ass,
    _cor_tag,
    _escape_ass,
    _font,
    _tempo_ass,
    libass_scale,
)
from src.highlight_captions.planner import (
    MAX_PALAVRAS_DESTAQUE,
    ItemDestaque,
)
from src.highlight_captions.style import HighlightStyle
from src.images import PalavraGlobal

if TYPE_CHECKING:
    from src.images import PlanoImagens
    from src.project import Project

log = logging.getLogger(__name__)
Caixa = tuple[int, int, int, int]
CaixasOcupadas = Callable[[float, float], Sequence[Caixa]]


def _largura(texto: str, fonte: str, tamanho: int) -> float:
    font = _font(fonte, tamanho)
    return float(font.getlength(texto)) if font is not None else len(texto) * tamanho * 0.62


def _quebras(
    palavras: Sequence[str], fonte: str, tamanho: int, largura_max: int
) -> tuple[list[int], int]:
    """Índices da primeira palavra de cada linha após a primeira."""
    inicios: list[int] = []
    linha = ""
    largura_linha = 0.0
    for i, palavra in enumerate(palavras):
        proxima = f"{linha} {palavra}" if linha else palavra
        largura = _largura(proxima, fonte, tamanho)
        if linha and largura > largura_max:
            inicios.append(i)
            linha = palavra
            largura_linha = max(largura_linha, _largura(linha, fonte, tamanho))
        else:
            linha = proxima
            largura_linha = max(largura_linha, largura)
    return inicios, math.ceil(largura_linha)


def _layout(
    palavras: Sequence[str], style: HighlightStyle, saida: tuple[int, int]
) -> tuple[int, list[int], int, int]:
    """Encontra fonte e dimensões de um bloco curto que cabe na área segura."""
    largura, altura = saida
    largura_max = min(largura - 2 * style.margem_lateral, round(largura * 0.78))
    if largura_max <= 0:
        raise ValueError("margens do destaque deixam a área de texto vazia")
    minimo = max(12, round(style.tamanho * 0.6))
    for tamanho in range(style.tamanho, minimo - 1, -1):
        quebras, maior = _quebras(palavras, style.fonte, tamanho, largura_max)
        n_linhas = len(quebras) + 1
        altura_bloco = round(n_linhas * 1.35 * tamanho)
        if maior <= largura_max and n_linhas <= 2 and altura_bloco <= altura * 0.28:
            return tamanho, quebras, maior, altura_bloco
    raise ValueError("trecho longo demais para a área de legenda de destaque")


def _intersecao(a: Caixa, b: Caixa) -> int:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(
        0, min(ay + ah, by + bh) - max(ay, by)
    )


def _posicao(
    saida: tuple[int, int],
    largura_texto: int,
    altura_texto: int,
    margem: int,
    caixas: Sequence[Caixa],
) -> tuple[int, int, int]:
    """Escolhe uma posição estável para toda a frase, afastada das áreas ocupadas."""
    largura, altura = saida
    topo = max(12, round(altura * 0.09))
    inferior = min(round(altura * 0.72), altura - altura_texto - margem)
    meio = min(round(altura * 0.48), altura - altura_texto - margem)
    candidatos = [
        (8, largura // 2, topo, (largura - largura_texto) // 2),
        (7, margem, topo, margem),
        (9, largura - margem, topo, largura - margem - largura_texto),
        (8, largura // 2, inferior, (largura - largura_texto) // 2),
        (7, margem, meio, margem),
        (9, largura - margem, meio, largura - margem - largura_texto),
    ]
    folga = max(8, round(altura * 0.025))
    ocupadas = [
        (x - folga, y - folga, w + 2 * folga, h + 2 * folga)
        for x, y, w, h in caixas
    ]
    def pontuacao(candidato: tuple[int, int, int, int]) -> int:
        _, _, y, x_esquerda = candidato
        bloco = (x_esquerda, y, largura_texto, altura_texto)
        return sum(_intersecao(bloco, caixa) for caixa in ocupadas)

    alinhamento, x, y, _ = min(candidatos, key=pontuacao)
    return alinhamento, x, y


def _texto_visivel(
    palavras: Sequence[str], quantidade: int, quebras: Sequence[int], style: HighlightStyle
) -> str:
    partes: list[str] = []
    for i, palavra in enumerate(palavras[:quantidade]):
        if i:
            partes.append(r"\N" if i in quebras else " ")
        escapada = _escape_ass(palavra)
        if i == quantidade - 1 and quantidade <= len(palavras):
            partes.append(
                f"{{\\c{_cor_tag(style.cor_entrada)}}}{escapada}{{\\c{_cor_tag(style.cor)}}}"
            )
        else:
            partes.append(escapada)
    return "".join(partes)


def _palavras_do_item(
    item: ItemDestaque,
    por_indice: dict[int, PalavraGlobal],
    limites_segmentos: Sequence[tuple[float, float]],
) -> list[PalavraGlobal]:
    if not 0 <= item.segmento < len(limites_segmentos):
        raise ValueError(f"destaque {item.id}: trecho mantido não existe")
    limite_inicio, limite_fim = limites_segmentos[item.segmento]
    if item.inicio < limite_inicio - 1e-6 or item.fim > limite_fim + 1e-6:
        raise ValueError(f"destaque {item.id}: frase atravessa uma emenda")
    try:
        palavras = [
            por_indice[i] for i in range(item.trecho_inicio_palavra, item.trecho_fim_palavra + 1)
        ]
    except KeyError as exc:
        raise ValueError(f"destaque {item.id}: palavra {exc.args[0]} ausente") from None
    if not palavras or any(
        p.clipe != item.clipe
        or p.segmento != item.segmento
        or p.palavra.inicio < limite_inicio - 1e-6
        or p.palavra.fim > limite_fim + 1e-6
        for p in palavras
    ):
        raise ValueError(f"destaque {item.id}: palavras atravessam uma emenda")
    if len(palavras) > MAX_PALAVRAS_DESTAQUE or len(item.texto.split()) > MAX_PALAVRAS_DESTAQUE:
        raise ValueError(f"destaque {item.id}: máximo de {MAX_PALAVRAS_DESTAQUE} palavras")
    if (
        abs(palavras[0].palavra.inicio - item.inicio) > 1e-6
        or abs(palavras[-1].palavra.fim - item.fim) > 1e-6
        or " ".join(p.palavra.texto for p in palavras) != item.texto
        or any(
            a.palavra.inicio >= b.palavra.inicio
            for a, b in zip(palavras, palavras[1:], strict=False)
        )
    ):
        raise ValueError(f"destaque {item.id}: plano não bate com a transcrição")
    return palavras


def build_highlight_ass(
    itens: Sequence[ItemDestaque],
    palavras: Sequence[PalavraGlobal],
    limites_segmentos: Sequence[tuple[float, float]],
    style: HighlightStyle | None = None,
    saida: tuple[int, int] = (1080, 1920),
    caixas_ocupadas: CaixasOcupadas | None = None,
) -> str:
    """Cria eventos progressivos e permanência, limitados pelas emendas."""
    style = (style or HighlightStyle()).for_output(saida)
    largura, altura = saida
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {largura}
PlayResY: {altura}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Destaque,{style.fonte},{round(style.tamanho / libass_scale(style.fonte))},{_cor_ass(style.cor)},{_cor_ass(style.cor_entrada)},{_cor_ass(style.cor_contorno)},{_cor_ass("#000000", 0x80)},-1,0,0,0,100,100,0,0,1,{style.contorno:g},{style.sombra:g},8,{style.margem_lateral},{style.margem_lateral},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""  # noqa: E501
    por_indice = {palavra.indice: palavra for palavra in palavras}
    selecionados = sorted((item for item in itens if item.ativo), key=lambda item: item.inicio)
    linhas: list[str] = []
    ultimo_fim = -math.inf
    for posicao, item in enumerate(selecionados):
        if item.inicio < ultimo_fim - 1e-6:
            raise ValueError("destaques sobrepostos")
        ws = _palavras_do_item(item, por_indice, limites_segmentos)
        textos = [p.palavra.texto for p in ws]
        tamanho, quebras, largura_texto, altura_texto = _layout(textos, style, saida)
        permanencia = (
            item.duracao_permanencia
            if item.duracao_permanencia is not None
            else style.duracao_permanencia
        )
        fim_permanencia = item.fim + permanencia
        caixas = (
            caixas_ocupadas(item.inicio, fim_permanencia)
            if caixas_ocupadas is not None
            else ()
        )
        alinhamento, x, y = _posicao(
            saida, largura_texto, altura_texto, style.margem_lateral, caixas
        )
        prefixo = (
            f"{{\\an{alinhamento}\\pos({x},{y})"
            f"\\fs{round(tamanho / libass_scale(style.fonte))}}}"
        )
        proximo = selecionados[posicao + 1].inicio if posicao + 1 < len(selecionados) else math.inf
        if fim_permanencia > limites_segmentos[item.segmento][1] + 1e-6:
            raise ValueError(
                f"destaque {item.id}: sem espaço para permanência de "
                f"{permanencia:g} s antes da emenda"
            )
        if fim_permanencia > proximo + 1e-6:
            raise ValueError(f"destaque {item.id}: permanência sobrepõe o próximo destaque")
        for i, palavra in enumerate(ws):
            inicio = palavra.palavra.inicio
            fim = ws[i + 1].palavra.inicio if i + 1 < len(ws) else item.fim
            if _tempo_ass(fim) <= _tempo_ass(inicio):
                continue
            texto = _texto_visivel(textos, i + 1, quebras, style)
            linhas.append(
                f"Dialogue: 0,{_tempo_ass(inicio)},{_tempo_ass(fim)},Destaque,,0,0,0,,"
                f"{prefixo}{texto}"
            )
        if _tempo_ass(fim_permanencia) > _tempo_ass(item.fim):
            fade_ms = round(min(style.fade_saida, fim_permanencia - item.fim) * 1000)
            fade = f"{{\\fad(0,{fade_ms})}}" if fade_ms else ""
            texto = _texto_visivel(textos, len(textos) + 1, quebras, style)
            linhas.append(
                f"Dialogue: 0,{_tempo_ass(item.fim)},{_tempo_ass(fim_permanencia)},"
                f"Destaque,,0,0,0,,{prefixo}{fade}{texto}"
            )
        ultimo_fim = item.fim
    return header + "\n".join(linhas) + ("\n" if linhas else "")


def write_highlight_ass(
    itens: Sequence[ItemDestaque],
    palavras: Sequence[PalavraGlobal],
    limites_segmentos: Sequence[tuple[float, float]],
    path: Path,
    style: HighlightStyle | None = None,
    saida: tuple[int, int] = (1080, 1920),
    caixas_ocupadas: CaixasOcupadas | None = None,
) -> Path:
    """Valida o plano e grava a legenda de destaque no disco."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conteudo = build_highlight_ass(
        itens, palavras, limites_segmentos, style, saida, caixas_ocupadas
    )
    path.write_text(conteudo, encoding="utf-8")
    return path


def write_highlights_project(
    project: Project,
    plano: PlanoImagens,
    transcriber: Callable[[Path], object],
    path: Path,
    style: HighlightStyle | None = None,
    saida: tuple[int, int] = (1080, 1920),
    fps: int = 30,
    caixas_ocupadas: CaixasOcupadas | None = None,
) -> Path:
    """Usa o plano salvo e a transcrição global já cortada em `t_out`."""
    from src.cuts import TimeMap
    from src.images import global_words, timeline_signature

    if plano.assinatura != timeline_signature(project):
        raise ValueError("plano de destaques obsoleto: os cortes mudaram")
    if fps <= 0:
        raise ValueError("fps precisa ser positivo")
    for clipe in project.timeline.clipes:
        for inicio, fim in clipe.trechos:
            if abs(inicio - round(inicio * fps) / fps) > 0.51e-6 or abs(
                fim - round(fim * fps) / fps
            ) > 0.51e-6:
                raise ValueError(
                    "cortes fora da grade de quadros: ajuste os trechos ao fps do render "
                    "antes de gerar destaques"
                )
    segmentos = TimeMap(project.timeline).segments
    limites = [(segmento.out_inicio, segmento.out_fim) for segmento in segmentos]
    return write_highlight_ass(
        plano.destaques,
        global_words(project, transcriber),
        limites,
        path,
        style,
        saida,
        caixas_ocupadas,
    )
