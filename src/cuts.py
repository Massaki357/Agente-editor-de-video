"""Cortes por silêncio, cortes de erros de fala (LLM) e remap de tempo.

Trechos mantidos saem das palavras do Whisper, refinados pelo `silencedetect` do
FFmpeg (o Whisper antecipa o início da palavra que vem depois de uma pausa). O LLM
só marca índices de palavras a remover (falsos começos, repetições, takes errados);
o tempo exato do corte é decidido aqui.
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

import numpy as np
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
    janela_vale: float = Field(
        0.15, ge=0, description="busca (s), para a frente, do vale de energia nas bordas"
    )
    folga_fade: float = Field(
        0.03,
        ge=0,
        description="silêncio (s) deixado entre a borda de um corte de fala e a palavra "
        "mantida, para o fade da emenda não apagar o fim/ataque dela",
    )
    max_corte_fala: float = Field(
        0.3, ge=0, le=1, description="fração máxima do clipe que o LLM pode remover"
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


# --------------------------------------------------------------------------- energia

ENERGIA_HOP = 0.01  # s
ENERGIA_SR = 16_000
VALE_DB = 10.0  # profundidade mínima de um vale entre palavras, em relação ao pico


@dataclass(frozen=True)
class Envelope:
    """RMS do áudio a cada `hop` segundos (janela de 2*hop), suavizado."""

    hop: float
    rms: np.ndarray

    def quiet_run(
        self,
        t: float,
        janela: float,
        lo: float = -math.inf,
        hi: float = math.inf,
        lado: Literal["primeiro", "ultimo"] = "ultimo",
        antes: float = 0.04,
    ) -> Intervalo | None:
        """Trecho silencioso entre palavras perto de `t`, em [t-antes, t+janela] ∩ [lo, hi].

        - A busca é quase toda para a frente: o Whisper marca o fim das palavras cedo
          (medido: 0,03–0,2 s); um vale antes de `t` costuma estar antes da última sílaba.
        - "Silencioso" é até 2x o mínimo da janela **e** pelo menos `VALE_DB` abaixo do pico
          da região. Em fala vozeada contínua não há trecho assim → None (usar o timestamp
          do Whisper; um extremo da janela comeria a palavra vizinha: "ideal é" → "é").
        - `primeiro`: o 1º trecho silencioso (logo depois de a palavra anterior acabar);
          `ultimo`: o último (logo antes de a próxima começar). O mínimo absoluto pode ficar
          antes da cauda da vogal ("gente" → sobra "te").
        Devolve (início, fim) do trecho silencioso escolhido.
        """
        a = max(t - antes, lo, 0.0)
        b = min(t + janela, hi, (len(self.rms) - 1) * self.hop)
        i0, i1 = int(math.ceil(a / self.hop)), int(math.floor(b / self.hop))
        if i1 <= i0:
            return None
        trecho = self.rms[i0 : i1 + 1]
        r0 = max(0, int((t - janela) / self.hop))
        r1 = min(len(self.rms), int((t + janela) / self.hop) + 1)
        pico = float(self.rms[r0:r1].max())
        limite = min(2 * float(trecho.min()), pico * 10 ** (-VALE_DB / 20))
        quietos = np.flatnonzero(trecho <= limite + 1e-6)
        if len(quietos) == 0:
            return None
        # separa em trechos contíguos e escolhe o primeiro ou o último
        quebras = np.flatnonzero(np.diff(quietos) > 1)
        inicios = np.concatenate([[0], quebras + 1])
        fins = np.concatenate([quebras, [len(quietos) - 1]])
        k = 0 if lado == "primeiro" else len(inicios) - 1
        return (
            float((quietos[inicios[k]] + i0) * self.hop),
            float((quietos[fins[k]] + i0) * self.hop),
        )

    def valley(
        self,
        t: float,
        janela: float,
        lo: float = -math.inf,
        hi: float = math.inf,
        lado: Literal["primeiro", "ultimo"] = "ultimo",
        antes: float = 0.04,
    ) -> float:
        """Borda do trecho silencioso de `quiet_run` colada à fala; `t` se não há vale."""
        run = self.quiet_run(t, janela, lo, hi, lado, antes)
        if run is None:
            return t
        return run[0] if lado == "primeiro" else run[1]


def energy_envelope(path: str | Path, settings: Settings | None = None) -> Envelope | None:
    """Envelope de energia do áudio do clipe (em cache). None se não há áudio."""
    settings = settings or get_settings()
    path = Path(path)
    key = f"{file_hash(path)[:32]}-{int(ENERGIA_HOP * 1000)}ms"
    cached = read_json_cache("energia", key, cache_dir=settings.cache_dir)
    if cached is not None:
        return Envelope(cached["hop"], np.asarray(cached["rms"], dtype=np.float32))
    if not probe_clip(path).tem_audio:
        return None

    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path)]
    cmd += ["-vn", "-ac", "1", "-ar", str(ENERGIA_SR), "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou ao ler o áudio de {path.name}")
    amostras = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)

    hop = int(ENERGIA_HOP * ENERGIA_SR)
    n = max(0, len(amostras) // hop - 1)
    quadros = np.lib.stride_tricks.sliding_window_view(amostras, 2 * hop)[::hop][:n]
    rms = np.sqrt((quadros**2).mean(axis=1)) if n else np.zeros(0, dtype=np.float32)
    rms = np.convolve(rms, np.ones(3) / 3, mode="same")  # suaviza 30 ms
    # O quadro i cobre [i*hop, i*hop + 2*hop]: centro em (i+1)*hop.
    rms = np.concatenate([[rms[0] if n else 0.0], rms]).astype(np.float32)

    write_json_cache(
        "energia", key, {"hop": ENERGIA_HOP, "rms": np.round(rms, 1).tolist()}, settings.cache_dir
    )
    return Envelope(ENERGIA_HOP, rms)


# --------------------------------------------------------------------------- trechos


def speech_from_words(
    palavras: Sequence[Palavra], min_silencio: float, remover: set[int] | None = None
) -> list[Intervalo]:
    """Junta palavras separadas por menos de `min_silencio` em blocos de fala.

    Palavras em `remover` (índices) ficam de fora e sempre quebram o bloco.
    """
    remover = remover or set()
    blocos: list[list[float]] = []
    quebra = False
    for p in palavras:
        if p.indice in remover:
            quebra = True
            continue
        if blocos and not quebra and p.inicio - blocos[-1][1] < min_silencio:
            blocos[-1][1] = max(blocos[-1][1], p.fim)
        else:
            blocos.append([p.inicio, p.fim])
        quebra = False
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
    remover: Sequence[tuple[int, int]] = (),
    energia: Envelope | None = None,
) -> list[Trecho]:
    """Trechos mantidos de um clipe, com margem, na grade de frames e sem sobreposição.

    `remover` são intervalos inclusivos de índices de palavras (erros de fala) que saem
    do vídeo mesmo sem pausa ao redor; com `energia`, as bordas desses cortes vão para o
    vale de energia entre as palavras (o Whisper erra a borda em dezenas de ms, o que
    deixaria sílabas soltas). Sem palavras (clipe sem fala reconhecida ou sem áudio),
    mantém o que não é silêncio.
    """
    silences = [(s, e) for s, e in silences if e - s >= params.silencio_borda]
    removidos = {i for a, b in remover for i in range(a, b + 1)}
    if palavras:
        fala = refine_with_silences(
            speech_from_words(palavras, params.min_silencio, removidos),
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
    if remover:
        spans = removal_spans(
            palavras, remover, energia, params.janela_vale, fps=fps, folga=params.folga_fade
        )
        trechos = _subtrair(trechos, spans, fps, na_grade=energia is not None)
    return [(round(a, 6), round(b, 6)) for a, b in trechos if b - a >= params.trecho_minimo - 1e-9]


def removal_spans(
    palavras: Sequence[Palavra],
    remover: Sequence[tuple[int, int]],
    energia: Envelope | None = None,
    janela: float = 0.15,
    fps: int = 30,
    folga: float = 0.03,
) -> list[Intervalo]:
    """Tempo (t_src) de cada intervalo de palavras removidas, sem invadir as vizinhas.

    Com `energia`, as bordas vão para o silêncio entre as palavras (`Envelope.quiet_run`)
    e já saem na grade de frames: o trecho mantido anterior termina no **fim** do silêncio
    que segue a palavra anterior, e o seguinte recomeça no **começo** do silêncio que
    antecede a próxima — assim o fade da emenda cai no silêncio. Silêncio estreito demais
    (ou nenhum): a borda vai para dentro do corte; sobrar um resto da palavra removida é
    melhor que comer a mantida.
    """
    por_indice = {p.indice: p for p in palavras}
    spans = []
    for i0, i1 in remover:
        primeira, ultima = por_indice[i0], por_indice[i1]
        inicio, fim = primeira.inicio, ultima.fim
        anterior, seguinte = por_indice.get(i0 - 1), por_indice.get(i1 + 1)
        if anterior is not None:
            inicio = max(inicio, anterior.fim)
        if seguinte is not None:
            fim = min(fim, seguinte.inicio)
        if energia is not None:
            lo = _meio(anterior) if anterior is not None else inicio - janela
            run = energia.quiet_run(inicio, janela, lo, _meio(primeira), lado="primeiro")
            inicio = _borda_saida(run, inicio, fps, folga)
            hi = _meio(seguinte) if seguinte is not None else fim + janela
            run = energia.quiet_run(fim, janela, _meio(ultima), hi, lado="ultimo")
            fim = _borda_entrada(run, fim, fps, folga)
        if fim > inicio:
            spans.append((inicio, fim))
    return spans


def _meio(p: Palavra) -> float:
    return (p.inicio + p.fim) / 2


def _borda_saida(run: Intervalo | None, t: float, fps: int, folga: float) -> float:
    """Onde termina o trecho mantido ANTES do corte (frame), dado o silêncio `run`."""
    if run is not None:
        g = math.floor(run[1] * fps + 1e-6) / fps  # fim do silêncio, na grade
        if g >= run[0] + folga:  # fade de saída cabe no silêncio
            return g
        t = run[1]
    return math.ceil(t * fps - 1e-6) / fps  # para dentro do corte


def _borda_entrada(run: Intervalo | None, t: float, fps: int, folga: float) -> float:
    """Onde recomeça o trecho mantido DEPOIS do corte (frame), dado o silêncio `run`."""
    if run is not None:
        g = math.ceil(run[0] * fps - 1e-6) / fps  # começo do silêncio, na grade
        if g <= run[1] - folga:  # fade de entrada cabe no silêncio
            return g
        t = run[0]
    return math.floor(t * fps + 1e-6) / fps  # para dentro do corte


def _subtrair(
    trechos: list[list[float]],
    spans: Sequence[Intervalo],
    fps: int,
    na_grade: bool = False,
) -> list[list[float]]:
    """Remove os `spans` dos trechos, com as novas bordas na grade de frames.

    Sem alinhamento de energia, arredonda para fora do span (nada da palavra removida
    sobra). Com `na_grade`, as bordas já foram escolhidas na grade por `removal_spans`.
    """
    for cs, ce in spans:
        if na_grade:
            cs_g, ce_g = round(cs * fps) / fps, round(ce * fps) / fps
        else:
            cs_g = math.floor(cs * fps + 1e-6) / fps
            ce_g = math.ceil(ce * fps - 1e-6) / fps
        if ce_g <= cs_g:
            continue
        novos = []
        for a, b in trechos:
            if b <= cs_g or a >= ce_g:
                novos.append([a, b])
                continue
            if a < cs_g:
                novos.append([a, cs_g])
            if b > ce_g:
                novos.append([ce_g, b])
        trechos = novos
    return trechos


# --------------------------------------------------------------------------- erros de fala

CORTES_FALA_VERSION = 1


def format_transcript(palavras: Sequence[Palavra]) -> str:
    """Transcrição indexada que o LLM recebe: `índice<TAB>início<TAB>palavra` por linha."""
    return "\n".join(f"{p.indice}\t{p.inicio:.2f}\t{p.texto}" for p in palavras)


def validate_speech_cuts(
    cortes: Sequence[tuple[int, int]],
    palavras: Sequence[Palavra],
    duracao: float,
    params: CutParams,
    nome: str = "",
) -> list[tuple[int, int]]:
    """Valida os intervalos do LLM. Qualquer problema descarta a resposta inteira."""
    if not cortes:
        return []
    indices = {p.indice for p in palavras}
    ordenados = sorted(cortes)
    for i0, i1 in ordenados:
        if i0 > i1 or i0 not in indices or i1 not in indices:
            log.warning("%s: LLM devolveu índices inválidos [%d, %d]; ignorando.", nome, i0, i1)
            return []
    for (_, fim_ant), (ini, _) in zip(ordenados, ordenados[1:], strict=False):
        if ini <= fim_ant:
            log.warning("%s: LLM devolveu intervalos sobrepostos; ignorando.", nome)
            return []

    mesclados: list[list[int]] = []
    for i0, i1 in ordenados:
        if mesclados and i0 == mesclados[-1][1] + 1:
            mesclados[-1][1] = i1
        else:
            mesclados.append([i0, i1])
    resultado = [(a, b) for a, b in mesclados]

    removido = sum(b - a for a, b in removal_spans(palavras, resultado))
    if duracao > 0 and removido / duracao > params.max_corte_fala:
        log.warning(
            "%s: LLM quis remover %.0f%% do clipe (limite %.0f%%); ignorando.",
            nome,
            100 * removido / duracao,
            100 * params.max_corte_fala,
        )
        return []
    return resultado


def speech_error_cuts(
    palavras: Sequence[Palavra],
    duracao: float,
    params: CutParams,
    settings: Settings | None = None,
    nome: str = "",
) -> list[tuple[int, int]]:
    """Pergunta ao LLM quais palavras são erros de fala. Nunca lança: falha → []."""
    if not palavras:
        return []
    settings = settings or get_settings()
    try:
        return _speech_error_cuts(palavras, duracao, params, settings, nome)
    except Exception as exc:  # o pipeline nunca quebra por causa do LLM
        log.warning("%s: falha nos cortes de fala (%s); só cortes de silêncio.", nome, exc)
        return []


def _speech_error_cuts(
    palavras: Sequence[Palavra],
    duracao: float,
    params: CutParams,
    settings: Settings,
    nome: str,
) -> list[tuple[int, int]]:
    texto = format_transcript(palavras)
    prompt = (Path(__file__).parent / "llm" / "prompts" / "cortes_fala.md").read_text(
        encoding="utf-8"
    )
    key = hashlib.sha256(
        json.dumps([CORTES_FALA_VERSION, settings.llm_model, prompt, texto]).encode()
    ).hexdigest()[:40]

    cached = read_json_cache("llm_cortes", key, cache_dir=settings.cache_dir)
    if cached is not None:
        brutos = [tuple(c) for c in cached]
    else:
        from src.llm import client
        from src.llm.schemas import CortesFala

        try:
            resposta = client.run_structured("cortes_fala", texto, CortesFala, settings=settings)
        except client.LLMError as exc:
            log.warning("%s: LLM indisponível (%s); só cortes de silêncio.", nome, exc)
            return []
        brutos = [(c.indice_inicio, c.indice_fim) for c in resposta.cortes]
        for c in resposta.cortes:
            trecho = " ".join(
                p.texto for p in palavras if c.indice_inicio <= p.indice <= c.indice_fim
            )
            log.info("%s: LLM marcou [%s] (%s)", nome, trecho, c.motivo)
        write_json_cache("llm_cortes", key, brutos, cache_dir=settings.cache_dir)

    return validate_speech_cuts(brutos, palavras, duracao, params, nome)


# --------------------------------------------------------------------------- projeto


Transcriber = Callable[..., Transcricao]


def apply_cuts(
    project: Project,
    params: CutParams | None = None,
    *,
    settings: Settings | None = None,
    transcriber: Transcriber = transcribe_clip,
    cortes_fala: bool = True,
) -> Project:
    """Transcreve, detecta silêncios, pede ao LLM os erros de fala (se `cortes_fala`)
    e define os trechos mantidos de cada clipe."""
    params = params or CutParams(fps=(settings or get_settings()).output_fps)
    timeline = project.timeline
    for i, clip in enumerate(timeline.clipes):
        path = Path(clip.arquivo)
        meta = clip.meta or probe_clip(path)
        palavras = transcriber(path, settings=settings).palavras if meta.tem_audio else []
        silences = detect_silences(path, params, settings)
        remover = (
            speech_error_cuts(palavras, meta.duracao, params, settings, path.name)
            if cortes_fala
            else []
        )
        energia = energy_envelope(path, settings) if remover else None
        trechos = keep_segments(palavras, silences, meta.duracao, params, remover, energia)
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
