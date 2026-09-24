"""Parte 1, Etapa 3: a limpeza de áudio dentro do pipeline do editor.

Decisão medida nesta etapa: a limpeza vale para o **áudio do vídeo final**. Limpar antes
de transcrever piorou o reconhecimento nas três amostras medidas (o Whisper já treina com
ruído), então a transcrição continua usando o áudio original.
"""

import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from src.audio.optimize import AudioParams, cache_key, cached_audio
from src.config import Settings, get_settings
from src.pipeline import PipelineOptions, audio_do_clipe, audios_limpos, render_project
from src.project import Clip, ClipMeta, Project, Timeline
from src.render import plan_segments
from src.transcribe import Palavra, Transcricao, cached_transcription

from .conftest import make_video, requires_ffmpeg


def S(**kw) -> Settings:
    kw.setdefault("cache_dir", get_settings().cache_dir)
    return Settings(**kw)


def _projeto(arquivos: list[Path]) -> Project:
    clipes = [
        Clip(
            arquivo=str(a),
            trechos=[(0.0, 1.0)],
            meta=ClipMeta(duracao=1.0, largura=320, altura=180, fps=30.0, tem_audio=True),
        )
        for a in arquivos
    ]
    t = Timeline(clipes=clipes)
    t.recalcular_offsets()
    return Project(timeline=t)


# ------------------------------------------------------------------ opção desligada (padrão)


def test_cleaning_is_off_by_default(tmp_path):
    options = PipelineOptions()
    assert options.limpar_audio is False
    assert audio_do_clipe(tmp_path / "clipe.mp4", options) is None


@requires_ffmpeg
def test_the_transcription_always_uses_the_original_audio(tmp_path, monkeypatch):
    """Medido nesta etapa: limpar antes de transcrever piora o reconhecimento."""
    import src.pipeline as pipeline

    video = make_video(tmp_path / "clipe.mp4", duration=1.0)
    recebidos = []

    def transcribe(path, settings=None, on_progress=None):  # sem parâmetro de áudio
        recebidos.append(Path(path))
        return Transcricao(arquivo_hash="x", modelo="fake", duracao=1.0, palavras=[])

    monkeypatch.setattr(pipeline, "transcribe_clip", transcribe)
    pipeline.transcribe_project(_projeto([video]))
    assert recebidos == [video]


@requires_ffmpeg
def test_the_clean_audio_is_cached_per_clip(tmp_path):
    video = make_video(tmp_path / "clipe.mp4", duration=1.0)
    params = AudioParams(motor="afftdn")
    settings = S(cache_dir=tmp_path / "cache")

    primeiro = cached_audio(video, params, settings=settings)
    assert primeiro.is_file()
    esperado = cache_key(video, params)
    assert esperado in primeiro.name  # um arquivo por clipe + parâmetros

    mtime = primeiro.stat().st_mtime
    segundo = cached_audio(video, params, settings=settings)
    assert segundo == primeiro and segundo.stat().st_mtime == mtime  # reaproveitado

    outro = cached_audio(
        video, params.model_copy(update={"aggressiveness": 0.9}), settings=settings
    )
    assert outro != primeiro  # parâmetro diferente, arquivo diferente


@requires_ffmpeg
@pytest.mark.parametrize("motor", ["afftdn", "noisereduce"])
def test_cleaning_keeps_the_timeline(tmp_path, motor):
    """A limpeza não muda os tempos do clipe (o render conta com isso).

    O DeepFilterNet encurta algumas dezenas de ms no fim; no render o `apad` completa
    com silêncio, então a tolerância aqui é de 100 ms.
    """
    video = make_video(tmp_path / "clipe.mp4", duration=2.0)
    limpo = cached_audio(video, AudioParams(motor=motor), settings=S(cache_dir=tmp_path / "cache"))
    with wave.open(str(limpo), "rb") as w:
        duracao = w.getnframes() / w.getframerate()
    assert duracao == pytest.approx(2.0, abs=0.1)


# ------------------------------------------------------------------ áudio no vídeo final


@requires_ffmpeg
def test_the_final_video_keeps_the_original_audio_by_default(tmp_path):
    video = make_video(tmp_path / "clipe.mp4", duration=1.0)
    projeto = _projeto([video])
    assert audios_limpos(projeto, PipelineOptions()) == ({}, [])


@requires_ffmpeg
def test_with_the_option_on_the_video_gets_the_clean_audio(tmp_path):
    video = make_video(tmp_path / "clipe.mp4", duration=1.0)
    projeto = _projeto([video])
    options = PipelineOptions(limpar_audio=True, parametros_audio=AudioParams(motor="afftdn"))
    limpos, avisos = audios_limpos(projeto, options)
    assert set(limpos) == {0} and limpos[0].suffix == ".wav" and avisos == []

    # o render passa a trilha limpa para o trecho, mantendo o vídeo do clipe
    metas = [c.meta for c in projeto.timeline.clipes]
    (seg,) = plan_segments(projeto.timeline, metas, 30, limpos)
    assert seg.arquivo == video and seg.audio == limpos[0]


def test_clips_without_audio_are_skipped(tmp_path):
    clip = Clip(
        arquivo=str(tmp_path / "mudo.mp4"),
        trechos=[(0.0, 1.0)],
        meta=ClipMeta(duracao=1.0, largura=320, altura=180, fps=30.0, tem_audio=False),
    )
    projeto = Project(timeline=Timeline(clipes=[clip]))
    assert audios_limpos(projeto, PipelineOptions(limpar_audio=True)) == ({}, [])


@requires_ffmpeg
def test_a_failed_cleaning_falls_back_to_the_original_audio(tmp_path, monkeypatch):
    """Melhor um vídeo com o som de antes do que nenhum vídeo."""
    import src.pipeline as pipeline

    video = make_video(tmp_path / "clipe.mp4", duration=1.0)
    projeto = _projeto([video])

    def falha(*a, **k):
        raise RuntimeError("highpass falhou em clipe.mp4: disco cheio")

    monkeypatch.setattr(pipeline, "cached_audio", falha)
    limpos, avisos = audios_limpos(projeto, PipelineOptions(limpar_audio=True))
    assert limpos == {}  # o render segue com o áudio original
    assert len(avisos) == 1 and "não deu para limpar o áudio" in avisos[0]
    assert "clipe.mp4" in avisos[0]


# ------------------------------------------------------------------ transcrição e cache


@requires_ffmpeg
def test_cleaning_does_not_touch_the_transcription_cache(tmp_path, monkeypatch):
    """A transcrição do clipe continua sendo achada pelo caminho do clipe."""
    import src.transcribe as tr

    video = make_video(tmp_path / "clipe.mp4", duration=1.0)
    settings = S(cache_dir=tmp_path / "cache")
    cached_audio(video, AudioParams(motor="afftdn"), settings=settings)  # limpa o áudio

    def fake_uncached(path, settings, on_progress=None):
        return Transcricao(
            arquivo_hash="ignorado",
            modelo="fake",
            duracao=1.0,
            palavras=[Palavra(indice=0, texto="oi", inicio=0.1, fim=0.4)],
        )

    monkeypatch.setattr(tr, "_transcribe_uncached", fake_uncached)
    tr.transcribe_clip(video, settings=settings)
    achada = cached_transcription(video, settings)
    assert achada is not None and achada.palavras[0].texto == "oi"


# ------------------------------------------------------------------ render de ponta a ponta


@requires_ffmpeg
def test_turning_the_option_on_does_not_break_the_render(tmp_path):
    video = make_video(tmp_path / "clipe.mp4", duration=1.5)
    projeto = _projeto([video])
    saida = tmp_path / "final.mp4"
    options = PipelineOptions(
        limpar_audio=True,
        parametros_audio=AudioParams(motor="afftdn"),
        cortes=False,
        cortes_fala=False,
        reenquadrar=False,
        legendas=False,
        imagens=False,
        zooms=False,
    )
    render_project(projeto, saida, options)
    assert saida.is_file()
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration"]
        + ["-of", "csv=p=0", str(saida)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "audio" in streams and "video" in streams


@pytest.mark.integration
@requires_ffmpeg
def test_the_rendered_video_gets_the_clean_audio_in_sync(tmp_path):
    """Critério de aceite: ligar a opção não quebra nada e o vídeo sai com o áudio limpo.

    Mede no vídeo renderizado: o SNR melhora, o volume vai para o alvo e o áudio continua
    com a mesma duração do vídeo (a limpeza preserva a duração, então nada desalinha).
    """
    from src.audio import metrics
    from src.config import SAMPLES_DIR

    fontes = sorted(p for p in SAMPLES_DIR.glob("*.mp4"))
    if not fontes:
        pytest.skip("coloque um vídeo com voz em samples/ (veja testes-pendentes.md)")

    # clipe curto e ruidoso a partir do sample do usuário
    voz = metrics.to_wav(fontes[0], tmp_path / "voz.wav")
    dados, taxa = metrics.read_wav(voz)
    dados = dados[: taxa * 8]
    rng = np.random.default_rng(3)
    ruidoso = tmp_path / "ruidoso.wav"
    with wave.open(str(ruidoso), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        mistura = np.clip(0.6 * dados + rng.normal(0, 10 ** (-32 / 20), len(dados)), -1, 1)
        w.writeframes((mistura * 32767).astype(np.int16).tobytes())
    clipe = tmp_path / "clipe.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
        + ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=8"]
        + ["-i", str(ruidoso), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
        + ["-c:a", "aac", "-shortest", str(clipe)],
        check=True,
        capture_output=True,
    )

    timeline = Timeline(
        clipes=[
            Clip(
                arquivo=str(clipe),
                trechos=[(0.0, 8.0)],
                meta=ClipMeta(duracao=8.0, largura=320, altura=180, fps=30.0, tem_audio=True),
            )
        ]
    )
    timeline.recalcular_offsets()
    projeto = Project(timeline=timeline)
    base = dict(cortes=False, cortes_fala=False, reenquadrar=False, legendas=False, imagens=False,
                zooms=False)  # fmt: skip

    saidas = {}
    for nome, limpar in (("original", False), ("limpo", True)):
        destino = tmp_path / f"{nome}.mp4"
        render_project(projeto, destino, PipelineOptions(limpar_audio=limpar, **base))
        saidas[nome] = metrics.measure(metrics.to_wav(destino, tmp_path / f"{nome}.wav"))

    assert saidas["limpo"].snr > saidas["original"].snr + 10  # o chiado sumiu do vídeo

    # volume alvo: medido no próprio mp4 (converter para mono mudaria o valor)
    from src.audio import loudness

    volume = loudness.measure(tmp_path / "limpo.mp4")
    assert volume.i == pytest.approx(-16, abs=1.5)
    assert loudness.measure(tmp_path / "original.mp4").i < volume.i - 5  # estava baixo
    duracoes = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration"]
        + ["-of", "csv=p=0", str(tmp_path / "limpo.mp4")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    valores = [float(linha.split(",")[1]) for linha in duracoes if "," in linha]
    assert max(valores) - min(valores) < 0.2  # vídeo e áudio com a mesma duração
