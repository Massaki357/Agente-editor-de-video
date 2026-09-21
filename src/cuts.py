"""Cortes por silêncio e remap de tempo.

Trechos mantidos saem das palavras do Whisper, refinados pelo `silencedetect` do
FFmpeg (o Whisper antecipa o início da palavra que vem depois de uma pausa).
Todos os tempos de trechos ficam na grade de frames do vídeo final (1/fps), para
que o que o `TimeMap` calcula seja exatamente o que o render produz.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import math
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from src.cache import file_hash, read_json_cache, write_json_cache
from src.clips import probe_clip
from src.config import Settings, get_settings
from src.project import Project, Timeline, Trecho
from src.transcribe import Palavra, Transcricao, transcribe_clip

log = logging.getLogger(__name__)

Intervalo = tuple[float, float]


class CutParams(BaseModel):
    min_silencio: float = Field(0.4, gt=0, description="pausa mínima (s) que vira corte")
    margem: float = Field(0.08, ge=0, description="respiro (s) antes e depois de cada fala")
    ruido_db: float = Field(-35.0, description="limiar do silencedetect (dB)")
    trecho_minimo: float = Field(0.1, ge=0, description="trechos mais curtos são descartados")
    extensao_max: float = Field(
        0.5, ge=0, description="quanto a borda da fala pode crescer até encostar no silêncio real"
    )
    silencio_borda: float = Field(
        0.2,
        gt=0,
        description="silêncio mínimo para acertar bordas (menores são pausas dentro da palavra)",
    )
    fps: int = 30


# --------------------------------------------------------------------------- silêncios


def detect_silences(
    path: str | Path, params: CutParams, settings: Settings | None = None
) -> list[Intervalo]:
    """Silêncios do clipe via `silencedetect` (em cache por hash + parâmetros).

    Detecta pausas curtas (`silencio_borda`) para acertar as bordas da fala; só as de
    pelo menos `min_silencio` viram corte.
    """
    settings = settings or get_settings()
    path = Path(path)
    digest = hashlib.sha256(
        json.dumps({"db": params.ruido_db, "d": params.silencio_borda}).encode()
    ).hexdigest()[:8]
    key = f"{file_hash(path)[:32]}-{digest}"
    cached = read_json_cache("silencio", key, cache_dir=settings.cache_dir)
    if cached is not None:
        return [tuple(s) for s in cached]

    meta = probe_clip(path)
    if not meta.tem_audio:
        silences: list[Intervalo] = []
    else:
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-vn", "-af"]
        cmd += [f"silencedetect=noise={params.ruido_db}dB:d={params.silencio_borda}", "-f", "null"]
        proc = subprocess.run(
            [*cmd, "-"], capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if proc.returncode != 0:
            raise RuntimeError(f"silencedetect falhou em {path.name}: {proc.stderr[-2000:]}")
        silences = parse_silencedetect(proc.stderr, meta.duracao)

    write_json_cache("silencio", key, silences, cache_dir=settings.cache_dir)
    return silences


_SILENCE_RE = re.compile(r"silence_(start|end): (-?[\d.]+)")


def parse_silencedetect(stderr: str, duracao: float) -> list[Intervalo]:
    """Converte o log do silencedetect em intervalos. Silêncio sem fim vai até `duracao`."""
    silences: list[Intervalo] = []
    inicio: float | None = None
    for kind, value in _SILENCE_RE.findall(stderr):
        t = max(0.0, float(value))
        if kind == "start":
            inicio = t
        elif inicio is not None:
            silences.append((inicio, min(t, duracao)))
            inicio = None
    if inicio is not None and inicio < duracao:
        silences.append((inicio, duracao))
    return silences


# --------------------------------------------------------------------------- trechos


def speech_from_words(palavras: Sequence[Palavra], min_silencio: float) -> list[Intervalo]:
    """Junta palavras separadas por menos de `min_silencio` em blocos de fala."""
    blocos: list[list[float]] = []
    for p in palavras:
        if blocos and p.inicio - blocos[-1][1] < min_silencio:
            blocos[-1][1] = max(blocos[-1][1], p.fim)
        else:
            blocos.append([p.inicio, p.fim])
    return [(a, b) for a, b in blocos if b > a]


def refine_with_silences(
    fala: Sequence[Intervalo],
    silences: Sequence[Intervalo],
    min_silencio: float,
    extensao_max: float = 0.5,
) -> list[Intervalo]:
    """Ajusta as bordas da fala ao silêncio real e divide blocos que contêm uma pausa.

    O Whisper erra as bordas nos dois sentidos:
    1. a borda que cai dentro de um silêncio é puxada para fora dele;
    2. a que cai em som é estendida (até `extensao_max`) até encostar no silêncio vizinho,
       para não cortar o fim/começo audível da palavra. Só estende se esse silêncio
       está ao alcance (senão é ruído de fundo) e nunca a menos de
       `min_silencio` da fala vizinha, senão uma pausa vista pelo Whisper deixaria de
       ser cortada (ex.: pausa com respiração, que o silencedetect não enxerga).
    """
    silences = sorted(silences)

    ajustados: list[Intervalo] = []
    for a, b in fala:
        for s, e in silences:
            if s <= a < e:  # começou "dentro" do silêncio: a fala só volta em `e`
                a = e
            if s < b <= e:  # terminou dentro do silêncio: a fala acabou em `s`
                b = s
        if b > a:
            ajustados.append((a, b))

    estendidos: list[Intervalo] = []
    for i, (a, b) in enumerate(ajustados):
        limite_a = estendidos[-1][1] + min_silencio if estendidos else -math.inf
        limite_b = ajustados[i + 1][0] - min_silencio if i + 1 < len(ajustados) else math.inf
        # Só estende se o silêncio real está ao alcance; mais longe que isso é ruído de
        # fundo, não o fim/começo audível de uma palavra.
        fim_anterior = max((e for s, e in silences if e <= a), default=None)
        if fim_anterior is not None and a - fim_anterior <= extensao_max:
            a = min(a, max(fim_anterior, limite_a))
        inicio_seguinte = min((s for s, e in silences if s >= b), default=None)
        if inicio_seguinte is not None and inicio_seguinte - b <= extensao_max:
            b = max(b, min(inicio_seguinte, limite_b))
        estendidos.append((a, b))

    refinados: list[Intervalo] = []
    for a, b in estendidos:
        pedaco_inicio = a
        for s, e in silences:
            if a < s and e < b and e - s >= min_silencio:
                refinados.append((pedaco_inicio, s))
                pedaco_inicio = e
        refinados.append((pedaco_inicio, b))
    return [(a, b) for a, b in refinados if b > a]


def complement(silences: Sequence[Intervalo], duracao: float) -> list[Intervalo]:
    """Partes do clipe que não são silêncio."""
    partes, cursor = [], 0.0
    for s, e in sorted(silences):
        if s > cursor:
            partes.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duracao:
        partes.append((cursor, duracao))
    return partes


def keep_segments(
    palavras: Sequence[Palavra],
    silences: Sequence[Intervalo],
    duracao: float,
    params: CutParams,
) -> list[Trecho]:
    """Trechos mantidos de um clipe, com margem, na grade de frames e sem sobreposição.

    Sem palavras (clipe sem fala reconhecida ou sem áudio), mantém o que não é silêncio.
    """
    silences = [(s, e) for s, e in silences if e - s >= params.silencio_borda]
    if palavras:
        fala = refine_with_silences(
            speech_from_words(palavras, params.min_silencio),
            silences,
            params.min_silencio,
            params.extensao_max,
        )
    else:
        fala = complement([(s, e) for s, e in silences if e - s >= params.min_silencio], duracao)

    fps = params.fps
    ultimo_frame = math.floor(duracao * fps + 1e-6) / fps
    trechos: list[list[float]] = []
    for a, b in fala:
        a = math.floor(max(0.0, a - params.margem) * fps + 1e-6) / fps
        b = min(math.ceil((b + params.margem) * fps - 1e-6) / fps, ultimo_frame)
        if b <= a:
            continue
        if trechos and a <= trechos[-1][1]:
            trechos[-1][1] = max(trechos[-1][1], b)
        else:
            trechos.append([a, b])
    return [(round(a, 6), round(b, 6)) for a, b in trechos if b - a >= params.trecho_minimo - 1e-9]


# --------------------------------------------------------------------------- projeto


Transcriber = Callable[..., Transcricao]


def apply_cuts(
    project: Project,
    params: CutParams | None = None,
    *,
    settings: Settings | None = None,
    transcriber: Transcriber = transcribe_clip,
) -> Project:
    """Transcreve, detecta silêncios e define os trechos mantidos de cada clipe."""
    params = params or CutParams(fps=(settings or get_settings()).output_fps)
    timeline = project.timeline
    for i, clip in enumerate(timeline.clipes):
        path = Path(clip.arquivo)
        meta = clip.meta or probe_clip(path)
        palavras = transcriber(path, settings=settings).palavras if meta.tem_audio else []
        silences = detect_silences(path, params, settings)
        trechos = keep_segments(palavras, silences, meta.duracao, params)
        if not trechos:
            log.warning("%s: nenhum trecho com fala; o clipe some do vídeo.", path.name)
        removido = meta.duracao - sum(b - a for a, b in trechos)
        log.info(
            "%s: %d trecho(s), %.1f s removidos de %.1f s",
            path.name,
            len(trechos),
            removido,
            meta.duracao,
        )
        timeline.substituir_trechos(i, trechos)
    return project


# --------------------------------------------------------------------------- remap


@dataclass(frozen=True)
class MappedSegment:
    clip: int
    src_inicio: float
    src_fim: float
    out_inicio: float

    @property
    def duracao(self) -> float:
        return self.src_fim - self.src_inicio

    @property
    def out_fim(self) -> float:
        return self.out_inicio + self.duracao


Snap = Literal["none", "next", "prev"]


class TimeMap:
    """Converte tempos entre o clipe original (t_src) e o vídeo cortado final (t_out)."""

    def __init__(self, timeline: Timeline):
        self.segments: list[MappedSegment] = []
        for i, clip in enumerate(timeline.clipes):
            t_out = clip.offset
            for a, b in clip.trechos:
                self.segments.append(MappedSegment(i, a, b, t_out))
                t_out += b - a
        self._out_starts = [s.out_inicio for s in self.segments]

    @property
    def duracao(self) -> float:
        return self.segments[-1].out_fim if self.segments else 0.0

    def to_out(self, clip: int, t_src: float, snap: Snap = "none") -> float | None:
        """t_src do clipe → t_out. Em trecho cortado: None, ou a borda mais próxima (`snap`)."""
        segs = [s for s in self.segments if s.clip == clip]
        for s in segs:
            if s.src_inicio - 1e-9 <= t_src <= s.src_fim + 1e-9:
                return s.out_inicio + min(max(t_src - s.src_inicio, 0.0), s.duracao)
        if snap == "next":
            proximo = next((s for s in segs if s.src_inicio > t_src), None)
            return proximo.out_inicio if proximo else None
        if snap == "prev":
            anterior = next((s for s in reversed(segs) if s.src_fim < t_src), None)
            return anterior.out_fim if anterior else None
        return None

    def to_src(self, t_out: float) -> tuple[int, float]:
        """t_out → (índice do clipe, t_src). O fim exato do vídeo cai no último trecho."""
        if not self.segments or t_out < -1e-9 or t_out > self.duracao + 1e-9:
            raise ValueError(f"t_out={t_out} fora do vídeo (0..{self.duracao})")
        i = max(0, bisect.bisect_right(self._out_starts, t_out) - 1)
        s = self.segments[i]
        return s.clip, s.src_inicio + min(t_out - s.out_inicio, s.duracao)

    def clip_bounds(self, clip: int) -> Intervalo | None:
        """(t_out início, t_out fim) do clipe no vídeo final; None se ele sumiu."""
        segs = [s for s in self.segments if s.clip == clip]
        return (segs[0].out_inicio, segs[-1].out_fim) if segs else None

    def seams(self) -> list[float]:
        """t_out das emendas entre clipes (efeitos não devem atravessá-las)."""
        clipes = sorted({s.clip for s in self.segments})
        return [self.clip_bounds(c)[0] for c in clipes[1:]]  # type: ignore[index]

    def words_to_out(self, clip: int, palavras: Sequence[Palavra]) -> list[Palavra]:
        """Palavras de um clipe em t_out; palavras inteiramente cortadas são descartadas."""
        resultado = []
        for p in palavras:
            inicio = self.to_out(clip, p.inicio, snap="next")
            fim = self.to_out(clip, p.fim, snap="prev")
            if inicio is None or fim is None or fim <= inicio:
                continue
            resultado.append(p.model_copy(update={"inicio": inicio, "fim": fim}))
        return resultado
