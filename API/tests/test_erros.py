"""Etapa 11: mensagens de erro claras, avisos dos clipes, custo do LLM e frontend servido."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.app import montar_frontend
from src.api.tasks import avisos_do_projeto
from src.clips import ClipProbeError
from src.errors import mensagem_amigavel
from src.llm import pricing
from src.llm.client import LLMError
from src.project import Clip, ClipMeta, Project, Timeline
from src.render import RenderError
from src.transcribe import TranscriptionError

# ------------------------------------------------------------------ mensagens


@pytest.mark.parametrize(
    ("exc", "trecho"),
    [
        (RenderError("ffmpeg não encontrado no PATH"), "Instale o FFmpeg"),
        (FileNotFoundError(2, "No such file or directory: 'ffprobe'"), "Instale o FFmpeg"),
        # arquivo corrompido não é culpa do FFmpeg, mesmo com "ffmpeg" na mensagem
        (ClipProbeError("ffmpeg falhou: moov atom not found"), "não parece um vídeo válido"),
        # clipe movido/apagado: também não é "FFmpeg ausente"
        (
            ClipProbeError("ffprobe falhou em 2.mp4: 2.mp4: No such file or directory"),
            "não está mais onde foi importado",
        ),
        (ClipProbeError("arquivo do clipe não encontrado: C:/x/2.mp4"), "não está mais onde"),
        # 401 do banco de fotos não é a chave do LLM
        (RuntimeError("pexels respondeu 401 Unauthorized"), "PEXELS_API_KEY"),
        (RenderError("FFmpeg falhou ao codificar o trecho 3"), "FFmpeg falhou ao processar"),
        (LLMError("Error code: 401 - invalid_api_key"), "OPENAI_API_KEY"),
        (LLMError("Error code: 429 - insufficient_quota"), "limite de uso"),
        (LLMError("model `gpt-9` does not exist"), "modelo do LLM"),
        (TranscriptionError("Whisper falhou: CUBLAS_STATUS_NOT_INITIALIZED"), "WHISPER_DEVICE=cpu"),
        (OSError("No space left on device"), "espaço em disco"),
        (ConnectionError("Failed to resolve api.openai.com"), "Falha de conexão"),
    ],
)
def test_friendly_message_says_what_to_do(exc, trecho):
    msg = mensagem_amigavel(exc)
    assert trecho in msg
    assert "detalhe:" in msg  # o texto técnico continua acessível


def test_unknown_error_keeps_its_own_text():
    assert mensagem_amigavel(ValueError("trechos devem estar em ordem")) == (
        "trechos devem estar em ordem"
    )
    assert mensagem_amigavel(RuntimeError("")) == "RuntimeError"


def test_long_detail_is_trimmed():
    msg = mensagem_amigavel(LLMError("invalid api_key " + "x" * 500))
    assert msg.endswith("…)") and len(msg) < 600


# ------------------------------------------------------------------ avisos dos clipes


def _projeto(**meta) -> Project:
    base = dict(duracao=5.0, largura=1920, altura=1080, fps=30.0, tem_audio=True)
    clip = Clip(arquivo="a.mp4", trechos=[(0.0, 5.0)], meta=ClipMeta(**{**base, **meta}))
    return Project(timeline=Timeline(clipes=[clip]))


def test_clip_warnings_cover_audio_vfr_and_hdr():
    assert avisos_do_projeto(_projeto()) == []
    (sem_audio,) = avisos_do_projeto(_projeto(tem_audio=False))
    assert "sem áudio" in sem_audio and "1. a.mp4" in sem_audio
    (vfr,) = avisos_do_projeto(_projeto(vfr=True))
    assert "fps variável" in vfr
    (hdr,) = avisos_do_projeto(_projeto(hdr=True))
    assert "HDR" in hdr and "SDR" in hdr
    assert len(avisos_do_projeto(_projeto(tem_audio=False, vfr=True, hdr=True))) == 3


def test_project_without_metadata_has_no_warnings():
    p = Project(timeline=Timeline(clipes=[Clip(arquivo="a.mp4")]))
    assert avisos_do_projeto(p) == []


# ------------------------------------------------------------------ custo do LLM


def test_llm_usage_summary_with_and_without_price():
    totais = {"openai:gpt-5-mini": {"input_tokens": 1_000_000, "output_tokens": 0, "calls": 2}}
    uso = pricing.resumir(totais)
    assert uso is not None
    assert uso["chamadas"] == 2 and uso["tokens_entrada"] == 1_000_000
    assert uso["custo_usd"] == pytest.approx(0.25)
    assert "≈US$ 0,2500".replace(",", ".") in pricing.descrever(uso)

    desconhecido = pricing.resumir(
        {"outro:modelo": {"input_tokens": 10, "output_tokens": 5, "calls": 1}}
    )
    assert desconhecido is not None and desconhecido["custo_usd"] is None
    assert "US$" not in pricing.descrever(desconhecido)


def test_no_llm_call_means_no_summary():
    assert pricing.resumir({}) is None


def test_price_table_covers_the_models_offered_in_the_interface():
    from src.config import LLM_MODELS

    assert set(LLM_MODELS) <= set(pricing.PRECOS)


# ------------------------------------------------------------------ frontend servido


def test_api_serves_the_built_frontend_when_it_exists(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Editor</title>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    assert montar_frontend(app, tmp_path) is True
    client = TestClient(app)
    assert client.get("/").status_code == 200 and "Editor" in client.get("/").text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}  # a API continua na frente


def test_without_a_build_the_api_still_works(tmp_path):
    app = FastAPI()
    assert montar_frontend(app, tmp_path / "nao-existe") is False
    assert TestClient(app).get("/").status_code == 404
