"""Plano criativo: imagens, zooms e cutaways de B-roll.

Divisão de responsabilidades (princípio central do etapas.md):
- O **LLM** só escolhe *quais palavras* ganham imagem e a *query* de busca (em
  inglês), a partir da transcrição global (todos os clipes, tempos finais).
- O **código** decide *quando* (0,2 s antes da palavra, dentro do clipe em que ela
  foi dita, densidade máxima, 1,2–3 s) e *onde*: zonas candidatas do quadro
  1080x1920; descarta as que cruzam a caixa real do rosto (Etapa 5) durante o
  intervalo ou a caixa da legenda (Etapa 7) e usa a maior que sobra.

Zooms: o LLM aponta a palavra de ênfase e a duração; o código valida (1 a cada 8 s,
1–2,5 s, dentro do clipe: nunca atravessa emenda). A geometria do zoom (escala,
rampas, rosto sempre inteiro) fica em `reframe.py`.

O plano (`PlanoImagens`, imagens + zooms + B-roll) é salvo no projeto; trocar
uma imagem, mudar a query ou desativar um item não chama o LLM de novo. A `assinatura` muda
quando os trechos mudam; aí o plano precisa ser refeito (o LLM tem cache).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import unicodedata
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import requests
from pydantic import BaseModel, Field

from src.broll.planner import ItemBroll
from src.cache import atomic_write_text, file_hash, read_json_cache, write_json_cache
from src.config import Settings, get_settings
from src.cuts import TimeMap, visible_words
from src.highlight_captions.planner import ItemDestaque
from src.project import Project
from src.transcribe import Palavra

log = logging.getLogger(__name__)

PLANO_VERSION = 2  # 2: plano criativo (imagens + zooms)
BUSCA_VERSION = 2  # 2: fotos horizontais
Retangulo = tuple[int, int, int, int]  # x, y, w, h em pixels da saída


class ImageParams(BaseModel):
    intervalo_min: float = Field(3.0, ge=0, description="s mínimos entre o início de 2 imagens")
    duracao_min: float = Field(1.2, gt=0)
    duracao_max: float = Field(3.0, gt=0)
    antecedencia: float = Field(0.2, ge=0, description="a imagem entra antes da palavra (s)")
    candidatos: int = Field(5, ge=1, le=15, description="fotos buscadas por item (para trocar)")


class ZoomPlanParams(BaseModel):
    intervalo_min: float = Field(8.0, ge=0, description="s mínimos entre o início de 2 zooms")
    duracao_min: float = Field(1.0, gt=0)
    duracao_max: float = Field(2.5, gt=0)
    duracao_minima: float = Field(0.8, gt=0, description="zoom encurtado pela emenda: descarta")
    antecedencia: float = Field(0.15, ge=0, description="o zoom começa antes da palavra (s)")


class Candidato(BaseModel):
    fonte: Literal["pexels", "pixabay", "upload"]
    id: str
    url: str  # imagem para baixar (~1000 px)
    miniatura: str
    autor: str = ""
    pagina: str = ""  # página da foto (crédito)


class ItemImagem(BaseModel):
    id: int
    indice: int  # índice global da palavra na transcrição enviada ao LLM
    clipe: int
    palavra: str
    query: str
    inicio: float  # t_out
    duracao: float
    candidatos: list[Candidato] = Field(default_factory=list)
    escolhida: int = 0
    ativa: bool = True

    @property
    def fim(self) -> float:
        return self.inicio + self.duracao

    @property
    def candidato(self) -> Candidato | None:
        if not self.candidatos:
            return None
        return self.candidatos[min(self.escolhida, len(self.candidatos) - 1)]


class ItemZoom(BaseModel):
    id: int
    indice: int  # índice global da palavra de ênfase
    clipe: int
    palavra: str
    inicio: float  # t_out
    duracao: float
    ativo: bool = True

    @property
    def fim(self) -> float:
        return self.inicio + self.duracao


class PlanoImagens(BaseModel):
    """Plano criativo do projeto: imagens, zooms e cutaways de B-roll."""

    assinatura: str
    itens: list[ItemImagem] = Field(default_factory=list)
    zooms: list[ItemZoom] = Field(default_factory=list)
    broll: list[ItemBroll] = Field(default_factory=list)
    destaques: list[ItemDestaque] = Field(default_factory=list)
    versao_destaques: int = 0  # 0 em planos antigos, que precisam ser replanejados
    duracao_permanencia_destaques: float | None = None
    broll_intervalo_min: float = 8.0  # densidade usada ao planejar; mudança exige nova prévia

    def zoom_intervals(self) -> list[tuple[float, float]]:
        return [(z.inicio, z.fim) for z in self.zooms if z.ativo]


# --------------------------------------------------------------------------- transcrição


@dataclass(frozen=True)
class PalavraGlobal:
    indice: int
    clipe: int
    palavra: Palavra  # em t_out
    segmento: int = 0  # índice do trecho mantido na TimeMap (não atravessar emenda interna)


def timeline_signature(project: Project) -> str:
    """Muda quando o conteúdo ou os cortes mudam (o plano deixa de valer)."""
    partes = []
    for clip in project.timeline.clipes:
        path = Path(clip.arquivo)
        partes.append([file_hash(path)[:16] if path.exists() else clip.arquivo, clip.trechos])
    return hashlib.sha256(json.dumps([PLANO_VERSION, partes]).encode()).hexdigest()[:16]


def global_words(project: Project, transcriber: Callable[[Path], object]) -> list[PalavraGlobal]:
    """Palavras visíveis de todos os clipes, no tempo final, com índice global."""
    tm = TimeMap(project.timeline)
    inicios_segmentos = [s.out_inicio for s in tm.segments]
    resultado: list[PalavraGlobal] = []
    for i, clip in enumerate(project.timeline.clipes):
        if tm.clip_bounds(i) is None or (clip.meta is not None and not clip.meta.tem_audio):
            continue
        palavras = transcriber(Path(clip.arquivo)).palavras  # type: ignore[attr-defined]
        for p in visible_words(tm, i, palavras):
            segmento = bisect_right(inicios_segmentos, p.inicio + 1e-9) - 1
            resultado.append(PalavraGlobal(len(resultado), i, p, segmento))
    return resultado


def format_global_transcript(palavras: Sequence[PalavraGlobal]) -> str:
    return "\n".join(f"{g.indice}\t{g.palavra.inicio:.2f}\t{g.palavra.texto}" for g in palavras)


# --------------------------------------------------------------------------- plano (LLM)


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in t if not unicodedata.combining(c)))


SugestaoImagem = tuple[int, str, str, float]  # índice, palavra, query, duração
SugestaoZoom = tuple[int, str, float]  # índice, palavra, duração


def suggest(
    palavras: Sequence[PalavraGlobal], settings: Settings | None = None
) -> tuple[list[SugestaoImagem], list[SugestaoZoom]]:
    """Sugestões brutas do LLM (uma chamada só): imagens e zooms. Falha → ([], [])."""
    if not palavras:
        return [], []
    settings = settings or get_settings()
    texto = format_global_transcript(palavras)
    prompt = (Path(__file__).parent / "llm" / "prompts" / "plano_criativo.md").read_text(
        encoding="utf-8"
    )
    key = hashlib.sha256(
        json.dumps([PLANO_VERSION, settings.llm_model, prompt, texto]).encode()
    ).hexdigest()[:40]
    cached = read_json_cache("llm_criativo", key, cache_dir=settings.cache_dir)
    if cached is not None:
        imagens = [tuple(c) for c in cached["imagens"]]
        zooms = [tuple(c) for c in cached["zooms"]]
        return imagens, zooms  # type: ignore[return-value]

    from src.llm import client
    from src.llm.schemas import PlanoCriativo

    try:
        resposta = client.run_structured("plano_criativo", texto, PlanoCriativo, settings=settings)
    except client.LLMError as exc:
        log.warning("Plano criativo sem LLM (%s): nenhuma imagem nem zoom.", exc)
        return [], []
    imagens = [(s.indice, s.palavra, s.query.strip(), float(s.duracao)) for s in resposta.imagens]
    zooms = [(z.indice, z.palavra, float(z.duracao)) for z in resposta.zooms]
    write_json_cache(
        "llm_criativo", key, {"imagens": imagens, "zooms": zooms}, cache_dir=settings.cache_dir
    )
    return imagens, zooms


def _resolve_word(
    indice: int, palavra: str, por_indice: Mapping[int, PalavraGlobal]
) -> PalavraGlobal | None:
    """A palavra que o LLM apontou; se o índice errou por pouco, procura ao redor."""
    g = por_indice.get(indice)
    if g is None:
        return None
    if not _norm(palavra) or _norm(palavra) == _norm(g.palavra.texto):
        return g
    return next(
        (
            por_indice[j]
            for j in range(indice - 3, indice + 4)
            if j in por_indice and _norm(por_indice[j].palavra.texto) == _norm(palavra)
        ),
        None,
    )


def validate_suggestions(
    brutos: Sequence[SugestaoImagem],
    palavras: Sequence[PalavraGlobal],
    tm: TimeMap,
    params: ImageParams,
) -> list[ItemImagem]:
    """Aplica as regras do etapas.md em código. Item inválido é descartado (com log)."""
    por_indice = {g.indice: g for g in palavras}
    itens: list[ItemImagem] = []
    ultimo_inicio, ultimo_fim, ultima_query = -math.inf, -math.inf, ""
    for indice, palavra, query, duracao in sorted(brutos, key=lambda b: b[0]):
        g = _resolve_word(indice, palavra, por_indice)
        if g is None or not query:
            log.info("Imagem descartada: '%s' fora do índice %s ou sem query.", palavra, indice)
            continue
        limite = tm.clip_bounds(g.clipe)
        if limite is None:
            continue
        duracao = min(max(duracao, params.duracao_min), params.duracao_max)
        inicio = max(g.palavra.inicio - params.antecedencia, limite[0])
        fim = min(inicio + duracao, limite[1])  # nunca atravessa a emenda
        if fim - inicio < params.duracao_min - 1e-9:
            log.info("Imagem descartada: '%s' perto demais do fim do clipe.", palavra)
            continue
        if inicio < ultimo_inicio + params.intervalo_min or inicio < ultimo_fim:
            log.info("Imagem descartada: '%s' (densidade máxima).", palavra)
            continue
        if _norm(query) == ultima_query:
            log.info("Imagem descartada: '%s' repete a anterior.", query)
            continue
        itens.append(
            ItemImagem(
                id=len(itens),
                indice=g.indice,
                clipe=g.clipe,
                palavra=g.palavra.texto,
                query=query,
                inicio=round(inicio, 3),
                duracao=round(fim - inicio, 3),
            )
        )
        ultimo_inicio, ultimo_fim, ultima_query = inicio, fim, _norm(query)
    return itens


def validate_zooms(
    brutos: Sequence[SugestaoZoom],
    palavras: Sequence[PalavraGlobal],
    tm: TimeMap,
    params: ZoomPlanParams,
) -> list[ItemZoom]:
    """Regras dos zooms em código: 1 a cada 8 s, 1–2,5 s, sem atravessar emendas."""
    por_indice = {g.indice: g for g in palavras}
    zooms: list[ItemZoom] = []
    ultimo_inicio, ultimo_fim = -math.inf, -math.inf
    for indice, palavra, duracao in sorted(brutos, key=lambda b: b[0]):
        g = _resolve_word(indice, palavra, por_indice)
        if g is None:
            log.info("Zoom descartado: '%s' não está no índice %s.", palavra, indice)
            continue
        limite = tm.clip_bounds(g.clipe)
        if limite is None:
            continue
        duracao = min(max(duracao, params.duracao_min), params.duracao_max)
        inicio = max(g.palavra.inicio - params.antecedencia, limite[0])
        fim = min(inicio + duracao, limite[1])  # nunca atravessa a emenda
        if fim - inicio < params.duracao_minima - 1e-9:
            log.info("Zoom descartado: '%s' perto demais do fim do clipe.", palavra)
            continue
        if inicio < ultimo_inicio + params.intervalo_min or inicio < ultimo_fim:
            log.info("Zoom descartado: '%s' (densidade máxima).", palavra)
            continue
        zooms.append(
            ItemZoom(
                id=len(zooms),
                indice=g.indice,
                clipe=g.clipe,
                palavra=g.palavra.texto,
                inicio=round(inicio, 3),
                duracao=round(fim - inicio, 3),
            )
        )
        ultimo_inicio, ultimo_fim = inicio, fim
    return zooms


def plan_images(
    project: Project,
    transcriber: Callable[[Path], object],
    params: ImageParams | None = None,
    settings: Settings | None = None,
    zoom_params: ZoomPlanParams | None = None,
) -> PlanoImagens:
    """Plano completo: LLM + validação de imagens e zooms + candidatos (sem baixar)."""
    params = params or ImageParams()
    settings = settings or get_settings()
    palavras = global_words(project, transcriber)
    tm = TimeMap(project.timeline)
    sug_imagens, sug_zooms = suggest(palavras, settings)
    itens = validate_suggestions(sug_imagens, palavras, tm, params)
    zooms = validate_zooms(sug_zooms, palavras, tm, zoom_params or ZoomPlanParams())
    for item in itens:
        item.candidatos = search_images(item.query, params.candidatos, settings)
        item.ativa = bool(item.candidatos)
    log.info(
        "Plano criativo: %d imagem(ns), %d com foto; %d zoom(s)",
        len(itens),
        sum(1 for i in itens if i.ativa),
        len(zooms),
    )
    return PlanoImagens(assinatura=timeline_signature(project), itens=itens, zooms=zooms)


# --------------------------------------------------------------------------- busca e download


def search_images(query: str, n: int = 5, settings: Settings | None = None) -> list[Candidato]:
    """Pexels (principal) e Pixabay (se o Pexels não trouxer nada). Em cache por query."""
    settings = settings or get_settings()
    key = hashlib.sha256(
        json.dumps([BUSCA_VERSION, query.lower().strip(), n]).encode()
    ).hexdigest()[:24]
    cached = read_json_cache("img_busca", key, cache_dir=settings.cache_dir)
    if cached is not None:
        return [Candidato.model_validate(c) for c in cached]
    candidatos: list[Candidato] = []
    for fonte in (_pexels, _pixabay):
        try:
            candidatos = fonte(query, n, settings)
        except (requests.RequestException, ValueError, KeyError) as exc:
            log.warning("Busca de imagem falhou (%s, '%s'): %s", fonte.__name__, query, exc)
            candidatos = []
        if candidatos:
            break
    if candidatos:  # não guarda "nada encontrado" (pode ser falta de chave/rede)
        write_json_cache(
            "img_busca", key, [c.model_dump() for c in candidatos], cache_dir=settings.cache_dir
        )
    return candidatos


def _pexels(query: str, n: int, settings: Settings) -> list[Candidato]:
    if settings.pexels_api_key is None:
        return []
    r = requests.get(
        "https://api.pexels.com/v1/search",
        # horizontais preenchem melhor as zonas largas do topo do quadro vertical
        params={"query": query, "per_page": n, "orientation": "landscape"},
        headers={"Authorization": settings.pexels_api_key.get_secret_value()},
        timeout=20,
    )
    r.raise_for_status()
    return [
        Candidato(
            fonte="pexels",
            id=str(p["id"]),
            url=p["src"]["large"],
            miniatura=p["src"]["medium"],
            autor=p.get("photographer", ""),
            pagina=p.get("url", ""),
        )
        for p in r.json().get("photos", [])
    ]


def _pixabay(query: str, n: int, settings: Settings) -> list[Candidato]:
    if settings.pixabay_api_key is None:
        return []
    r = requests.get(
        "https://pixabay.com/api/",
        params={
            "key": settings.pixabay_api_key.get_secret_value(),
            "q": query,
            "image_type": "photo",
            "orientation": "horizontal",
            "per_page": max(3, n),
            "safesearch": "true",
        },
        timeout=20,
    )
    r.raise_for_status()
    return [
        Candidato(
            fonte="pixabay",
            id=str(h["id"]),
            url=h.get("largeImageURL") or h["webformatURL"],
            miniatura=h.get("previewURL") or h["webformatURL"],
            autor=h.get("user", ""),
            pagina=h.get("pageURL", ""),
        )
        for h in r.json().get("hits", [])[:n]
    ]


def download(url: str, settings: Settings | None = None) -> Path:
    """Baixa (uma vez) para `CACHE_DIR/imagens/`."""
    if not url.startswith("https://"):
        local = Path(url)
        if local.is_file():
            return local
        raise FileNotFoundError(f"imagem local indisponível: {url}")
    settings = settings or get_settings()
    destino = settings.cache_dir / "imagens" / f"{hashlib.sha1(url.encode()).hexdigest()}.img"
    if destino.exists() and destino.stat().st_size > 0:
        return destino
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".part")
    tmp.write_bytes(r.content)
    tmp.replace(destino)
    return destino


# --------------------------------------------------------------------------- aparência


def prepare_overlay(
    origem: Path,
    caixa: tuple[int, int],
    destino: Path,
    sticker: bool = False,
    settings: Settings | None = None,
) -> tuple[int, int]:
    """PNG RGBA pronto para o overlay, cabendo em `caixa` (w, h). Devolve o tamanho.

    Normal: cantos arredondados e borda branca. Sticker: fundo removido com rembg
    (em cache) e contorno branco.
    """
    from PIL import Image, ImageDraw, ImageFilter, ImageOps

    img = ImageOps.exif_transpose(Image.open(origem)).convert("RGBA")
    if sticker:
        try:
            img = _sticker(origem, img, settings or get_settings())
        except Exception as exc:  # rembg/onnxruntime falhou: usa a foto normal
            log.warning("Sticker indisponível (%s); usando a foto com borda.", exc)
            sticker = False
    borda = 10
    max_w, max_h = caixa[0] - 2 * borda, caixa[1] - 2 * borda
    escala = min(max_w / img.width, max_h / img.height)
    img = img.resize(
        (max(1, int(img.width * escala)), max(1, int(img.height * escala))), Image.LANCZOS
    )

    if sticker:
        alpha = img.getchannel("A")
        contorno = alpha.filter(ImageFilter.MaxFilter(2 * (borda // 2) + 1))
        fundo = Image.new("RGBA", img.size, (255, 255, 255, 0))
        fundo.putalpha(contorno)
        base = Image.new("RGBA", (img.width + 2 * borda, img.height + 2 * borda), (0, 0, 0, 0))
        base.alpha_composite(fundo, (borda, borda))
        base.alpha_composite(img, (borda, borda))
    else:
        raio = int(0.08 * min(img.size))
        mascara = Image.new("L", img.size, 0)
        ImageDraw.Draw(mascara).rounded_rectangle((0, 0, *img.size), raio, fill=255)
        img.putalpha(mascara)
        base = Image.new("RGBA", (img.width + 2 * borda, img.height + 2 * borda), (0, 0, 0, 0))
        ImageDraw.Draw(base).rounded_rectangle(
            (0, 0, base.width - 1, base.height - 1), raio + borda, fill=(255, 255, 255, 255)
        )
        base.alpha_composite(img, (borda, borda))
    destino.parent.mkdir(parents=True, exist_ok=True)
    base.save(destino)
    return base.size


def _sticker(origem: Path, img, settings: Settings):
    from PIL import Image

    cache = settings.cache_dir / "stickers" / f"{file_hash(origem)[:24]}.png"
    if cache.exists():
        return Image.open(cache).convert("RGBA")
    from rembg import remove

    recortada = remove(img)
    bbox = recortada.getchannel("A").getbbox()
    if bbox:
        recortada = recortada.crop(bbox)
    cache.parent.mkdir(parents=True, exist_ok=True)
    recortada.save(cache)
    return recortada


# --------------------------------------------------------------------------- posição


def candidate_zones(saida: tuple[int, int], legenda: Retangulo | None) -> dict[str, Retangulo]:
    """Zonas onde uma imagem pode ficar no quadro vertical (pixels da saída)."""
    w, h = saida
    m = int(0.04 * w)  # margem
    topo = int(0.07 * h)  # abaixo da barra de status / título do app
    meio = int(0.36 * h)
    lado = (w - 3 * m) // 2
    zonas = {
        "topo": (m, topo, w - 2 * m, int(0.28 * h)),
        "topo-esquerda": (m, topo, lado, int(0.24 * h)),
        "topo-direita": (2 * m + lado, topo, lado, int(0.24 * h)),
        "meio-esquerda": (m, meio, lado, int(0.22 * h)),
        "meio-direita": (2 * m + lado, meio, lado, int(0.22 * h)),
    }
    if legenda is not None:  # logo acima da legenda
        alt = int(0.2 * h)
        zonas["acima-legenda"] = (m, max(topo, legenda[1] - alt - m), w - 2 * m, alt)
    return zonas


def _intersecta(a: Retangulo, b: Retangulo, folga: int = 0) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw <= bx - folga
        or bx + bw + folga <= ax
        or ay + ah <= by - folga
        or by + bh + folga <= ay
    )


def choose_zone(
    zonas: Mapping[str, Retangulo], proibidos: Sequence[Retangulo], folga: int = 24
) -> tuple[str, Retangulo] | None:
    """A maior zona que não encosta em nenhum retângulo proibido (rosto, legenda)."""
    livres = [
        (nome, z)
        for nome, z in zonas.items()
        if not any(_intersecta(z, p, folga) for p in proibidos)
    ]
    if not livres:
        return None
    ordem = list(zonas)
    return max(livres, key=lambda nz: (nz[1][2] * nz[1][3], -ordem.index(nz[0])))


@dataclass(frozen=True)
class Overlay:
    """Uma imagem pronta para a passada 2 do render."""

    png: Path
    x: int  # canto superior esquerdo na saída
    y: int
    w: int
    h: int
    inicio: float  # t_out
    fim: float
    item_id: int


FaceBoxes = Callable[[float, float], list[Retangulo]]  # (t0, t1) → caixas do rosto na saída


def build_overlays(
    plano: PlanoImagens,
    saida: tuple[int, int],
    legenda: Retangulo | None,
    face_boxes: FaceBoxes,
    work_dir: Path,
    sticker: bool = False,
    settings: Settings | None = None,
) -> list[Overlay]:
    """Baixa, prepara e posiciona as imagens ativas do plano."""
    settings = settings or get_settings()
    zonas = candidate_zones(saida, legenda)
    overlays: list[Overlay] = []
    for item in plano.itens:
        cand = item.candidato
        if not item.ativa or cand is None:
            continue
        proibidos = list(face_boxes(item.inicio, item.fim))
        if legenda is not None:
            proibidos.append(legenda)
        escolha = choose_zone(zonas, proibidos)
        if escolha is None:
            log.warning("'%s': sem espaço livre (rosto/legenda); imagem pulada.", item.palavra)
            continue
        nome_zona, (zx, zy, zw, zh) = escolha
        try:
            origem = download(cand.url, settings)
            png = work_dir / f"imagem_{item.id}.png"
            w, h = prepare_overlay(origem, (zw, zh), png, sticker, settings)
        except (requests.RequestException, OSError, ValueError) as exc:
            log.warning("'%s': imagem indisponível (%s); pulada.", item.palavra, exc)
            continue
        x, y = zx + (zw - w) // 2, zy + (zh - h) // 2
        overlays.append(Overlay(png, x, y, w, h, item.inicio, item.fim, item.id))
        log.info(
            "Imagem '%s' (%s) em %s, %.2f–%.2f s",
            item.palavra,
            item.query,
            nome_zona,
            item.inicio,
            item.fim,
        )
    return overlays


def save_plan(plano: PlanoImagens, path: Path) -> Path:
    atomic_write_text(path, plano.model_dump_json(indent=2))
    return path


def load_plan(path: Path) -> PlanoImagens | None:
    try:
        return PlanoImagens.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
