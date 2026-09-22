"""Legendas estilizadas em `.ass`, palavra por palavra, para queimar no vídeo final.

- Entrada: palavras já no tempo do vídeo final (t_out), **por clipe** (via
  `cuts.TimeMap.words_to_out`). Os grupos são montados dentro de cada clipe e
  limitados ao intervalo dele, então nenhuma legenda atravessa uma emenda.
- Grupos de até 4 palavras (configurável). Um grupo fecha em pontuação final, em
  pausa longa ou quando o texto passaria da largura segura (medida com a fonte real).
- Cada palavra vira um evento: o grupo inteiro na tela, com a palavra atual
  destacada (cor diferente e leve aumento).
- `CaptionStyle.box()` devolve o retângulo da área de legenda em pixels da saída,
  para a Etapa 8 não colocar imagens em cima.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from src.config import PROJECT_ROOT
from src.transcribe import Palavra

FONTS_DIR = PROJECT_ROOT / "fonts"
FONTE_PADRAO = "Poppins"
ARQUIVO_FONTE = {"Poppins": "Poppins-Bold.ttf"}

_PONTUACAO_FINAL = re.compile(r"[.!?…]+[\"')\]]*$")
_LIMPA_BORDAS = re.compile(r"^[\"'(\[«“]+|[,;:.…\"')\]»”]+$")


class CaptionStyle(BaseModel):
    """Estilo das legendas (cores em #RRGGBB)."""

    fonte: str = FONTE_PADRAO
    tamanho: int = Field(
        84, ge=20, le=200, description="corpo da fonte (em) em px na saída 1920; maiúsculas ~0,7x"
    )
    cor: str = Field("#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    cor_destaque: str = Field("#FFD400", pattern=r"^#[0-9A-Fa-f]{6}$")
    cor_contorno: str = Field("#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    contorno: float = Field(7.0, ge=0, le=20)
    sombra: float = Field(3.0, ge=0, le=20)
    margem_inferior: int = Field(
        520, ge=0, le=1500, description="px entre a base da legenda e a borda de baixo"
    )
    margem_lateral: int = Field(70, ge=0, le=400)
    maiusculas: bool = True
    palavras_max: int = Field(4, ge=1, le=8)
    pausa_quebra: float = Field(0.45, ge=0, description="pausa (s) que força novo grupo")
    destaque_escala: int = Field(112, ge=100, le=150, description="% de aumento da palavra atual")

    @field_validator("fonte")
    @classmethod
    def _fonte_disponivel(cls, v: str) -> str:
        # fonte fora de fonts/ faria o libass trocar por outra em silêncio
        if v not in ARQUIVO_FONTE or not (FONTS_DIR / ARQUIVO_FONTE[v]).exists():
            raise ValueError(f"fonte '{v}' indisponível; disponíveis: {sorted(ARQUIVO_FONTE)}")
        return v

    def for_output(self, saida: tuple[int, int]) -> CaptionStyle:
        """O mesmo estilo proporcional a outra altura de saída (base: 1920 px)."""
        k = saida[1] / 1920
        if abs(k - 1) < 1e-6:
            return self
        return self.model_copy(
            update={
                "tamanho": max(20, round(self.tamanho * k)),
                "contorno": round(self.contorno * k, 2),
                "sombra": round(self.sombra * k, 2),
                "margem_inferior": round(self.margem_inferior * k),
                "margem_lateral": round(self.margem_lateral * k),
            }
        )

    def box(self, saida: tuple[int, int] = (1080, 1920)) -> tuple[int, int, int, int]:
        """(x, y, w, h) da área onde as legendas aparecem, em pixels da saída.

        Inclui contorno, sombra e o aumento da palavra destacada, com uma folga.
        """
        largura, altura = saida
        folga = int(self.contorno + self.sombra) + 12
        # altura da linha: ascendente + descendente da fonte (~1,4 em na Poppins)
        alt_linha = int(self.tamanho * 1.4 * self.destaque_escala / 100)
        y_base = altura - self.margem_inferior
        x = max(0, self.margem_lateral - folga)
        # acentos de maiúsculas (Í, Ê, À) sobem acima da linha: +0,15 em no topo
        y = max(0, y_base - alt_linha - folga - int(0.15 * self.tamanho))
        return x, y, min(largura, largura - 2 * x), min(y_base + folga - y, altura - y)


@dataclass(frozen=True)
class Grupo:
    palavras: list[Palavra]  # em t_out
    inicio: float
    fim: float


# --------------------------------------------------------------------------- texto


def texto_exibido(texto: str, maiusculas: bool) -> str:
    """Tira vírgulas/pontos das bordas (mantém ? e !) e aplica caixa alta."""
    t = _LIMPA_BORDAS.sub("", texto.strip())
    t = t.strip() or texto.strip()
    return t.upper() if maiusculas else t


def _escape_ass(texto: str) -> str:
    # O libass não tem escape para "\" (`\N` quebraria a linha, `\h` viraria espaço):
    # a barra invertida vira "/". Chaves abririam tags de override: viram parênteses.
    return texto.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


@lru_cache(maxsize=16)
def _font(fonte: str, tamanho: int):
    from PIL import ImageFont

    arquivo = FONTS_DIR / ARQUIVO_FONTE.get(fonte, f"{fonte}.ttf")
    try:
        return ImageFont.truetype(str(arquivo), tamanho)
    except OSError:
        return None


@lru_cache(maxsize=16)
def libass_scale(fonte: str) -> float:
    """Quanto do `Fontsize` do .ass vira corpo (em) da fonte no libass.

    O libass ajusta a fonte para que winAscent + winDescent (OS/2) caibam no
    `Fontsize`; o Pillow usa o em. Na Poppins, 1000 / (1135 + 627) = 0,5675: sem
    converter, a legenda sairia com ~57% do tamanho medido.
    """
    from fontTools.ttLib import TTFont

    arquivo = FONTS_DIR / ARQUIVO_FONTE.get(fonte, f"{fonte}.ttf")
    try:
        font = TTFont(str(arquivo), lazy=True)
        os2, upm = font["OS/2"], font["head"].unitsPerEm
        altura = os2.usWinAscent + os2.usWinDescent
        if altura <= 0:
            altura = font["hhea"].ascent - font["hhea"].descent
        return upm / altura
    except Exception:  # fonte ilegível: sem conversão (o validador já exige a fonte)
        return 1.0


def ass_font_size(style: CaptionStyle) -> int:
    """`Fontsize` a gravar no .ass para o corpo `style.tamanho` aparecer de verdade."""
    return round(style.tamanho / libass_scale(style.fonte))


def text_width(texto: str, style: CaptionStyle) -> float:
    """Largura em px do texto com a fonte do estilo (estimativa se a fonte faltar)."""
    font = _font(style.fonte, style.tamanho)
    if font is None:
        return len(texto) * style.tamanho * 0.62
    return float(font.getlength(texto))


# --------------------------------------------------------------------------- grupos


def group_words(
    palavras: Sequence[Palavra],
    style: CaptionStyle,
    limite: tuple[float, float] | None = None,
    saida: tuple[int, int] = (1080, 1920),
) -> list[Grupo]:
    """Agrupa as palavras de UM clipe (em t_out) em legendas curtas.

    `limite` = (início, fim) do clipe no vídeo final: nenhuma legenda sai dele.
    """
    largura_max = saida[0] - 2 * style.margem_lateral
    # o destaque aumenta a palavra atual: reserva o espaço
    largura_max /= style.destaque_escala / 100
    grupos: list[list[Palavra]] = []
    atual: list[Palavra] = []

    def cabe(ws: list[Palavra]) -> bool:
        texto = " ".join(texto_exibido(w.texto, style.maiusculas) for w in ws)
        return text_width(texto, style) <= largura_max

    for p in palavras:
        if atual:
            pausa = p.inicio - atual[-1].fim
            fecha_frase = bool(_PONTUACAO_FINAL.search(atual[-1].texto))
            cheio = len(atual) >= style.palavras_max
            fecha = cheio or fecha_frase or pausa >= style.pausa_quebra or not cabe([*atual, p])
            if fecha:
                grupos.append(atual)
                atual = []
        atual.append(p)
    if atual:
        grupos.append(atual)

    resultado: list[Grupo] = []
    for i, ws in enumerate(grupos):
        inicio = ws[0].inicio
        # fica na tela até a próxima legenda (no máximo 0,4 s depois da última palavra)
        proximo = grupos[i + 1][0].inicio if i + 1 < len(grupos) else float("inf")
        fim = min(ws[-1].fim + 0.4, proximo)
        if limite is not None:
            inicio, fim = max(inicio, limite[0]), min(fim, limite[1])
        if fim > inicio:
            resultado.append(Grupo(ws, inicio, fim))
    return resultado


# --------------------------------------------------------------------------- .ass


def _cor_ass(hex_rgb: str, alpha: int = 0) -> str:
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _cor_tag(hex_rgb: str) -> str:
    """Cor para a tag de override `\\c` (formato &HBBGGRR&, sem alfa)."""
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H{b}{g}{r}&".upper()


def _tempo_ass(t: float) -> str:
    """Tempo do .ass (centésimos), sempre arredondado para BAIXO.

    Arredondar para cima jogaria o fim de uma legenda para o 1º frame do clipe
    seguinte quando a emenda cai em k/30 s com k % 3 == 2 (ex.: 2,0667 → 2,07). Com
    todos os tempos para baixo, fins nunca avançam e o fim de uma palavra continua
    igual ao início da próxima (sem piscar entre eventos).
    """
    cs = max(0, int(math.floor(t * 100 + 1e-6)))
    h, resto = divmod(cs, 360_000)
    m, resto = divmod(resto, 6_000)
    s, cs = divmod(resto, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(
    grupos_por_clipe: Sequence[Sequence[Grupo]],
    style: CaptionStyle,
    saida: tuple[int, int] = (1080, 1920),
) -> str:
    """Arquivo .ass completo: um evento por palavra, com a palavra atual destacada."""
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
Style: Legenda,{style.fonte},{ass_font_size(style)},{_cor_ass(style.cor)},{_cor_ass(style.cor_destaque)},{_cor_ass(style.cor_contorno)},{_cor_ass("#000000", 0x80)},-1,0,0,0,100,100,0,0,1,{style.contorno:g},{style.sombra:g},2,{style.margem_lateral},{style.margem_lateral},{style.margem_inferior},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""  # noqa: E501
    largura_segura = largura - 2 * style.margem_lateral
    linhas = []
    for grupos in grupos_por_clipe:
        for g in grupos:
            exibidos = [texto_exibido(w.texto, style.maiusculas) for w in g.palavras]
            textos = [_escape_ass(t) for t in exibidos]
            # grupo que não cabe (ex.: uma palavra única muito longa) é reduzido
            ocupada = text_width(" ".join(exibidos), style) * style.destaque_escala / 100
            k_escala = min(1.0, largura_segura / ocupada) if ocupada else 1.0
            base = math.floor(100 * k_escala)
            real = math.floor(style.destaque_escala * k_escala)
            destaque = f"{{\\c{_cor_tag(style.cor_destaque)}\\fscx{real}\\fscy{real}}}"
            normal = f"{{\\c{_cor_tag(style.cor)}\\fscx{base}\\fscy{base}}}"
            prefixo = f"{{\\fscx{base}\\fscy{base}}}" if base != 100 else ""
            for k, w in enumerate(g.palavras):
                ini = g.inicio if k == 0 else max(w.inicio, g.inicio)
                fim = g.palavras[k + 1].inicio if k + 1 < len(g.palavras) else g.fim
                fim = min(max(fim, ini), g.fim)
                if fim - ini < 0.01:
                    continue
                partes = [f"{destaque}{t}{normal}" if j == k else t for j, t in enumerate(textos)]
                linhas.append(
                    f"Dialogue: 0,{_tempo_ass(ini)},{_tempo_ass(fim)},Legenda,,0,0,0,,"
                    + prefixo
                    + " ".join(partes)
                )
    return header + "\n".join(linhas) + ("\n" if linhas else "")


def write_captions(
    palavras_por_clipe: Sequence[tuple[Sequence[Palavra], tuple[float, float] | None]],
    path: Path,
    style: CaptionStyle | None = None,
    saida: tuple[int, int] = (1080, 1920),
) -> Path:
    """Gera e grava o .ass. Cada item: (palavras do clipe em t_out, limites do clipe)."""
    style = style or CaptionStyle()
    grupos = [group_words(ws, style, limite, saida) for ws, limite in palavras_por_clipe]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ass(grupos, style, saida), encoding="utf-8")
    return path
