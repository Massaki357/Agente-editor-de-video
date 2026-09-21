"""LLM real sobre `samples/2.mp4` (custa tokens; rode com `pytest -m integration`)."""

import pytest

from src.clips import probe_clip
from src.config import SAMPLES_DIR
from src.cuts import CutParams, removal_spans, speech_error_cuts
from src.transcribe import transcribe_clip

pytestmark = pytest.mark.integration


def test_t5_llm_marks_speech_errors_in_real_clip():
    clip = SAMPLES_DIR / "2.mp4"
    if not clip.exists():
        pytest.skip("falta samples/2.mp4 (veja testes-pendentes.md)")
    palavras = transcribe_clip(clip).palavras
    duracao = probe_clip(clip).duracao
    params = CutParams()
    cortes = speech_error_cuts(palavras, duracao, params, nome=clip.name)
    assert cortes, "o LLM não marcou nenhum erro de fala num clipe feito com erros"
    removido = sum(b - a for a, b in removal_spans(palavras, cortes))
    assert removido / duracao <= params.max_corte_fala
    for a, b in cortes:  # deixa o que foi marcado visível no relatório do pytest
        print(f"[{a}-{b}]", " ".join(p.texto for p in palavras[a : b + 1]))
