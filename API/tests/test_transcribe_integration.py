"""Testes com vídeos reais de `samples/` (lentos; rode com `pytest -m integration`).

Os arquivos esperados estão descritos em `testes-pendentes.md`.
"""

import re
import time
from pathlib import Path

import pytest

from src.clips import probe_clip
from src.config import SAMPLES_DIR
from src.transcribe import transcribe_clip

pytestmark = pytest.mark.integration

SAMPLES = SAMPLES_DIR
HESITACOES = {"é", "eh", "hm", "hum", "né", "ah", "ahn", "tipo"}


def _sample(name: str) -> Path:
    path = SAMPLES / name
    if not path.exists():
        pytest.skip(f"falta samples/{name} (veja testes-pendentes.md)")
    return path


def test_t1_real_transcription_and_cache():
    clip = _sample("1.mp4")
    first = transcribe_clip(clip, use_cache=False)
    assert first.palavras, "nenhuma palavra transcrita"
    tempos = [(p.inicio, p.fim) for p in first.palavras]
    assert all(a <= b for a, b in tempos)
    assert all(tempos[i][1] <= tempos[i + 1][0] + 1e-6 for i in range(len(tempos) - 1))
    assert tempos[-1][1] <= first.duracao + 0.5

    started = time.perf_counter()
    second = transcribe_clip(clip)
    assert time.perf_counter() - started < 1.0
    assert second == first


def test_t2_hesitations_are_kept():
    palavras = transcribe_clip(_sample("2.mp4")).palavras
    normalizadas = [re.sub(r"[^\wáéíóúâêôãõç]", "", p.texto.lower()) for p in palavras]
    encontradas = [w for w in normalizadas if w in HESITACOES]
    assert encontradas, "o Whisper removeu todas as hesitações"
    repeticoes = [a for a, b in zip(normalizadas, normalizadas[1:], strict=False) if a and a == b]
    assert repeticoes, "nenhuma repetição imediata ('o o') foi mantida"


def test_t3_portrait_phone_video_is_detected():
    meta = probe_clip(_sample("4.mp4"))
    assert meta.largura < meta.altura
