import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.transcribe as tr
from src.config import Settings, get_settings
from tests.conftest import make_video


class FakeModel:
    """Imita o WhisperModel: `transcribe` devolve (gerador de segmentos, info)."""

    def __init__(self, words, fail_on_iterate=False):
        self.words = words
        self.fail_on_iterate = fail_on_iterate
        self.calls: list[dict] = []

    def transcribe(self, audio, **kwargs):
        self.calls.append({"audio": audio, **kwargs})

        def segments():
            if self.fail_on_iterate:
                raise RuntimeError("Library cublas64_12.dll is not found")
            # um segmento por palavra: dá para ver o progresso dentro do clipe
            for w, s, e in self.words:
                yield SimpleNamespace(
                    end=e, words=[SimpleNamespace(word=w, start=s, end=e, probability=0.9)]
                )

        return segments(), SimpleNamespace(duration=2.0)


def S(**kw) -> Settings:
    """Settings com o CACHE_DIR isolado do teste (o padrão apontaria para o .cache real)."""
    return Settings(cache_dir=get_settings().cache_dir, **kw)


WORDS = [(" Olá", 0.10, 0.40), (" é...", 0.50, 0.90), (" ", 0.9, 0.9), (" mundo.", 1.00, 1.50)]


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    return make_video(tmp_path_factory.mktemp("tr") / "fala.mp4", duration=2.0)


@pytest.fixture
def fake(monkeypatch):
    models: dict[str, FakeModel] = {}

    def load(model_name, device):
        return models.setdefault(device, FakeModel(WORDS))

    monkeypatch.setattr(tr, "load_model", load)
    monkeypatch.setattr(tr, "resolve_device", lambda d: "cpu" if d == "cpu" else "cuda")
    return models


def test_output_has_indexed_words_with_times(clip, fake):
    result = tr.transcribe_clip(clip, settings=S())
    assert [(p.indice, p.texto) for p in result.palavras] == [
        (0, "Olá"),
        (1, "é..."),
        (2, "mundo."),
    ]
    assert result.palavras[0].inicio == pytest.approx(0.10)
    assert result.palavras[2].fim == pytest.approx(1.50)
    assert result.texto == "Olá é... mundo."


def test_whisper_called_with_fixed_decisions(clip, fake):
    tr.transcribe_clip(clip, settings=S(whisper_model="small"))
    call = fake["cuda"].calls[0]
    assert call["language"] == "pt"
    assert call["word_timestamps"] is True
    assert call["vad_filter"] is True
    assert "né" in call["initial_prompt"] and "hm" in call["initial_prompt"]


def test_second_run_uses_cache_and_is_fast(clip, fake, monkeypatch):
    first = tr.transcribe_clip(clip, settings=S())

    def boom(*a, **k):
        raise AssertionError("não deveria transcrever de novo")

    monkeypatch.setattr(tr, "_transcribe_uncached", boom)
    started = time.perf_counter()
    second = tr.transcribe_clip(clip, settings=S())
    assert time.perf_counter() - started < 1.0
    assert second == first


def test_changing_model_invalidates_cache(clip, fake):
    tr.transcribe_clip(clip, settings=S(whisper_model="small"))
    tr.transcribe_clip(clip, settings=S(whisper_model="medium"))
    assert len(fake["cuda"].calls) == 2


def test_use_cache_false_forces_retranscription(clip, fake):
    tr.transcribe_clip(clip, settings=S())
    tr.transcribe_clip(clip, settings=S(), use_cache=False)
    assert len(fake["cuda"].calls) == 2


def test_gpu_failure_falls_back_to_cpu_when_auto(clip, monkeypatch):
    models = {"cuda": FakeModel(WORDS, fail_on_iterate=True), "cpu": FakeModel(WORDS)}
    monkeypatch.setattr(tr, "load_model", lambda name, device: models[device])
    monkeypatch.setattr(tr, "resolve_device", lambda d: "cuda")
    result = tr.transcribe_clip(clip, settings=S(whisper_device="auto"))
    assert len(result.palavras) == 3
    assert len(models["cpu"].calls) == 1


def test_gpu_failure_raises_when_cuda_is_forced(clip, monkeypatch):
    monkeypatch.setattr(tr, "load_model", lambda n, d: FakeModel(WORDS, fail_on_iterate=True))
    with pytest.raises(tr.TranscriptionError):
        tr.transcribe_clip(clip, settings=S(whisper_device="cuda"))


def test_clip_without_audio_gives_empty_transcription(tmp_path, fake):
    silent = make_video(tmp_path / "mudo.mp4", audio=False)
    result = tr.transcribe_clip(silent, settings=S())
    assert result.palavras == []
    assert fake == {}  # o modelo nem foi carregado


def test_extract_audio_is_16k_mono_wav(clip, tmp_path):
    import wave

    wav = tmp_path / "a.wav"
    assert tr.extract_audio(clip, wav) is True
    with wave.open(str(wav)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)


def test_build_words_fixes_overlaps_and_reindexes():
    palavras = tr.build_words(
        [
            {"texto": " a", "inicio": 1.0, "fim": 1.5},
            {"texto": "", "inicio": 1.5, "fim": 1.6},
            {"texto": " b", "inicio": 1.4, "fim": 1.3},  # começa antes do fim anterior
        ]
    )
    assert [(p.indice, p.texto, p.inicio, p.fim) for p in palavras] == [
        (0, "a", 1.0, 1.5),
        (1, "b", 1.5, 1.5),
    ]


def test_settings_cache_dir_is_respected(clip, fake, tmp_path):
    custom = tmp_path / "meu_cache"
    tr.transcribe_clip(clip, settings=Settings(cache_dir=custom))
    assert len(list((custom / "transcricao").glob("*.json"))) == 1


def test_cache_key_is_short():
    key = tr.cache_key("a" * 64, Settings())
    assert len(key) <= 41


def test_non_runtime_gpu_error_also_falls_back(clip, monkeypatch):
    class BadModel(FakeModel):
        def transcribe(self, audio, **kwargs):
            raise ValueError("float16 compute type not supported")

    models = {"cuda": BadModel(WORDS), "cpu": FakeModel(WORDS)}
    monkeypatch.setattr(tr, "load_model", lambda name, device: models[device])
    monkeypatch.setattr(tr, "resolve_device", lambda d: "cuda")
    assert len(tr.transcribe_clip(clip, settings=S()).palavras) == 3


def test_clip_without_audio_keeps_video_duration(tmp_path, fake):
    silent = make_video(tmp_path / "mudo2.mp4", duration=1.5, audio=False)
    assert tr.transcribe_clip(silent, settings=S()).duracao == pytest.approx(1.5, abs=0.1)


def test_progress_is_reported_while_transcribing(clip, fake):
    """Etapa 11: progresso dentro do clipe (antes só havia progresso entre clipes)."""
    fracoes: list[float] = []
    t = tr.transcribe_clip(clip, settings=S(), on_progress=fracoes.append)
    assert len(t.palavras) == 3
    assert len(fracoes) == len(WORDS)  # um aviso por segmento
    assert fracoes == sorted(fracoes) and 0 < fracoes[0] < 1 and fracoes[-1] == pytest.approx(0.75)


class Cancelado(Exception):
    """Faz o papel do JobCancelled da API."""


@pytest.mark.parametrize("device", ["cuda", "auto", "cpu"])
def test_cancelling_never_falls_back_to_the_cpu_nor_becomes_an_error(clip, fake, device):
    """Cancelar não pode virar 'Whisper falhou' nem re-transcrever tudo na CPU."""

    def cancelar(_fracao: float) -> None:
        raise Cancelado()

    with pytest.raises(Cancelado):  # a exceção original chega a quem chamou
        tr.transcribe_clip(clip, settings=S(whisper_device=device), on_progress=cancelar)
    assert len(fake) == 1  # um device só: o fallback GPU → CPU não foi disparado
