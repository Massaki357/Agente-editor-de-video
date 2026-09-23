"""Parte 1, Etapa 1: cadeia highpass → denoise → loudnorm, com cache."""

import wave
from pathlib import Path

import numpy as np
import pytest

from src.audio import deepfilter, loudness, metrics
from src.audio import denoise as denoise_mod
from src.audio.optimize import AudioParams, cache_key, optimize_audio
from src.config import Settings, get_settings

from .conftest import requires_ffmpeg


def S(**kw) -> Settings:
    """Settings do teste; `cache_dir` padrão é o cache isolado do conftest."""
    kw.setdefault("cache_dir", get_settings().cache_dir)
    return Settings(**kw)


def wav_ruidoso(
    path: Path, *, ruido_db: float = -40.0, dur: float = 3.0, ganho: float = 0.3
) -> Path:
    """WAV 48k mono com 'fala' (tom modulado) sobre chiado — serve para medir níveis."""
    taxa = 48_000
    t = np.arange(int(taxa * dur)) / taxa
    rng = np.random.default_rng(11)
    fala = (np.sin(2 * np.pi * 2 * t) > 0).astype(float) * np.sin(2 * np.pi * 200 * t)
    dados = np.clip(ganho * fala + rng.normal(0, 10 ** (ruido_db / 20), len(t)), -1, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes((dados * 32767).astype(np.int16).tobytes())
    return path


# ------------------------------------------------------------------ aggressiveness


@pytest.mark.parametrize(
    ("aggressiveness", "esperado"),
    [(0.0, 0.0), (0.25, 10.0), (0.5, 20.0), (0.75, 60.0), (1.0, 100.0)],
)
def test_aggressiveness_maps_to_decibels(aggressiveness, esperado):
    assert denoise_mod.atenuacao_db(aggressiveness) == pytest.approx(esperado)


def test_aggressiveness_out_of_range_is_clamped():
    assert denoise_mod.atenuacao_db(-1) == 0.0
    assert denoise_mod.atenuacao_db(5) == 100.0


# ------------------------------------------------------------------ escolha do motor


def test_engine_order_prefers_deepfilternet(monkeypatch):
    monkeypatch.setattr(denoise_mod.deepfilter, "asset_da_plataforma", lambda: "algum.exe")
    assert denoise_mod.motores_disponiveis(S())[0] == "deepfilternet"
    assert denoise_mod.motores_disponiveis(S())[-1] == "afftdn"  # sempre há o FFmpeg


def test_without_a_binary_the_chain_still_has_engines(monkeypatch):
    monkeypatch.setattr(denoise_mod.deepfilter, "asset_da_plataforma", lambda: None)
    motores = denoise_mod.motores_disponiveis(S())
    assert "deepfilternet" not in motores and motores


def test_a_broken_engine_falls_back_to_the_next(tmp_path, monkeypatch):
    entrada = wav_ruidoso(tmp_path / "ruido.wav")

    def quebrado(*a, **k):
        raise OSError("motor pifou")  # falha esperada de motor

    chamados: list[str] = []

    def fake_afftdn(ent, sai, aggressiveness):
        chamados.append("afftdn")
        sai.write_bytes(Path(ent).read_bytes())

    monkeypatch.setattr(denoise_mod.deepfilter, "enhance", quebrado)
    monkeypatch.setattr(denoise_mod, "_noisereduce", quebrado)
    monkeypatch.setattr(denoise_mod, "_afftdn", fake_afftdn)
    _, motor = denoise_mod.denoise(entrada, tmp_path / "saida.wav", settings=S())
    assert motor == "afftdn" and chamados == ["afftdn"]


def test_all_engines_broken_raises_with_the_reasons(tmp_path, monkeypatch):
    entrada = wav_ruidoso(tmp_path / "ruido.wav")

    def quebrado(*a, **k):
        raise OSError("motor pifou")

    for alvo in ("_noisereduce", "_afftdn"):
        monkeypatch.setattr(denoise_mod, alvo, quebrado)
    monkeypatch.setattr(denoise_mod.deepfilter, "enhance", quebrado)
    with pytest.raises(denoise_mod.DenoiseError, match="motor pifou"):
        denoise_mod.denoise(entrada, tmp_path / "saida.wav", settings=S())


def test_a_programming_error_is_not_swallowed_by_the_fallback(tmp_path, monkeypatch):
    """Bug nosso (TypeError) tem que estourar, não virar 'degradação silenciosa'."""
    entrada = wav_ruidoso(tmp_path / "ruido.wav")

    def bug(*a, **k):
        raise TypeError("assinatura errada")

    monkeypatch.setattr(denoise_mod.deepfilter, "asset_da_plataforma", lambda: "algum.exe")
    monkeypatch.setattr(denoise_mod.deepfilter, "enhance", bug)
    with pytest.raises(TypeError):
        denoise_mod.denoise(entrada, tmp_path / "saida.wav", settings=S())


def test_zero_aggressiveness_copies_the_audio(tmp_path):
    entrada = wav_ruidoso(tmp_path / "ruido.wav")
    saida, motor = denoise_mod.denoise(
        entrada, tmp_path / "igual.wav", aggressiveness=0, settings=S()
    )
    assert motor == "nenhum" and saida.read_bytes() == entrada.read_bytes()


@requires_ffmpeg
def test_noisereduce_and_afftdn_lower_the_noise_floor(tmp_path):
    """Os dois motores de fallback funcionam de verdade (sem rede, sem binário)."""
    entrada = wav_ruidoso(tmp_path / "ruido.wav", ruido_db=-30)
    antes = metrics.measure(entrada)
    for motor in ("noisereduce", "afftdn"):
        saida, usado = denoise_mod.denoise(
            entrada, tmp_path / f"{motor}.wav", motor=motor, settings=S()
        )
        assert usado == motor
        assert metrics.measure(saida).ruido_db < antes.ruido_db - 5


# ------------------------------------------------------------------ loudnorm


def test_loudnorm_json_is_parsed_even_with_ffmpeg_noise_around_it():
    stderr = "\n".join(
        [
            "frame= 10",
            "[Parsed_loudnorm_0 @ 0] ",
            "{",
            '  "input_i" : "-27.5",',
            '  "input_tp" : "-9.9",',
            '  "input_lra" : "5.0",',
            '  "input_thresh" : "-37.6",',
            '  "target_offset" : "0.4"',
            "}",
        ]
    )
    m = loudness.parse_loudnorm(stderr)
    assert (m.i, m.tp, m.lra, m.thresh, m.offset) == (-27.5, -9.9, 5.0, -37.6, 0.4)


def test_silent_input_does_not_break_the_measurement():
    stderr = (
        '{"input_i" : "-inf", "input_tp" : "-inf", "input_lra" : "0.0", '
        '"input_thresh" : "-inf", "target_offset" : "0.0"}'
    )
    m = loudness.parse_loudnorm(stderr)
    assert m.i == -70.0 and m.tp == -70.0


def test_output_without_json_gives_a_clear_error():
    with pytest.raises(loudness.LoudnessError, match="não imprimiu as medidas"):
        loudness.parse_loudnorm("Conversion failed!")


@requires_ffmpeg
def test_two_pass_normalization_hits_the_target(tmp_path):
    entrada = wav_ruidoso(tmp_path / "baixo.wav", ruido_db=-60, ganho=0.05)  # bem baixo
    medida = loudness.measure(entrada)
    loudness.normalize(entrada, tmp_path / "alto.wav", alvo_lufs=-16)
    depois = loudness.measure(tmp_path / "alto.wav")
    assert medida.i < -25  # entrada estava baixa mesmo
    assert depois.i == pytest.approx(-16, abs=1.5)
    assert depois.tp <= -1.0  # o pico real respeita o teto


# ------------------------------------------------------------------ cadeia completa


@requires_ffmpeg
def test_chain_cleans_and_normalizes(tmp_path):
    entrada = wav_ruidoso(tmp_path / "ruido.wav", ruido_db=-30, ganho=0.05)
    r = optimize_audio(
        entrada, tmp_path / "limpo.wav", AudioParams(motor="afftdn"), settings=S(cache_dir=tmp_path)
    )
    assert r.motor == "afftdn" and r.caminho.is_file()
    assert r.ganho_snr_db > 5  # o fundo caiu em relação à fala
    assert r.lufs_depois == pytest.approx(-16, abs=1.5)
    assert r.lufs_antes < r.lufs_depois  # estava baixo e subiu
    assert not r.do_cache


@requires_ffmpeg
def test_second_run_comes_from_the_cache(tmp_path, monkeypatch):
    entrada = wav_ruidoso(tmp_path / "ruido.wav")
    params = AudioParams(motor="afftdn")
    settings = S(cache_dir=tmp_path / "cache")
    primeiro = optimize_audio(entrada, tmp_path / "a.wav", params, settings=settings)
    assert not primeiro.do_cache

    def proibido(*a, **k):
        raise AssertionError("o cache deveria ter evitado o reprocessamento")

    monkeypatch.setattr("src.audio.optimize.denoise_mod.denoise", proibido)
    segundo = optimize_audio(entrada, tmp_path / "b.wav", params, settings=settings)
    assert segundo.do_cache
    assert segundo.motor == primeiro.motor
    assert segundo.depois.snr == pytest.approx(primeiro.depois.snr)
    assert (tmp_path / "b.wav").read_bytes() == (tmp_path / "a.wav").read_bytes()


@requires_ffmpeg
def test_changing_a_parameter_invalidates_the_cache(tmp_path):
    entrada = wav_ruidoso(tmp_path / "ruido.wav")
    base = AudioParams(motor="afftdn")
    assert cache_key(entrada, base) != cache_key(
        entrada, base.model_copy(update={"alvo_lufs": -14})
    )
    assert cache_key(entrada, base) != cache_key(
        entrada, base.model_copy(update={"aggressiveness": 0.9})
    )
    assert cache_key(entrada, base) == cache_key(entrada, AudioParams(motor="afftdn"))

    settings = S(cache_dir=tmp_path / "cache")
    optimize_audio(entrada, tmp_path / "a.wav", base, settings=settings)
    outro = optimize_audio(
        entrada, tmp_path / "b.wav", base.model_copy(update={"alvo_lufs": -20}), settings=settings
    )
    assert not outro.do_cache and outro.lufs_depois == pytest.approx(-20, abs=1.5)


@requires_ffmpeg
def test_a_better_engine_invalidates_the_cache(tmp_path, monkeypatch):
    """O binário do DeepFilterNet é baixado no 1º uso (e o download pode falhar): se a 1ª
    execução caiu no fallback, a seguinte não pode ficar presa nele.

    O que muda no mundo real é o `enhance` passar a funcionar — a lista de motores
    disponíveis é a mesma nas duas execuções, então é ela que o teste mantém intacta.
    """
    entrada = wav_ruidoso(tmp_path / "ruido.wav")
    params = AudioParams()  # motor automático
    settings = S(cache_dir=tmp_path / "cache")

    def sem_binario(*a, **k):
        raise deepfilter.DeepFilterIndisponivel("falha ao baixar o DeepFilterNet")

    monkeypatch.setattr(denoise_mod.deepfilter, "enhance", sem_binario)
    primeiro = optimize_audio(entrada, tmp_path / "a.wav", params, settings=settings)
    assert primeiro.motor != "deepfilternet" and not primeiro.do_cache

    def enhance_ok(ent, sai, settings=None, **kw):
        Path(sai).write_bytes(Path(ent).read_bytes())
        return Path(sai)

    monkeypatch.setattr(denoise_mod.deepfilter, "enhance", enhance_ok)
    segundo = optimize_audio(entrada, tmp_path / "b.wav", params, settings=settings)
    assert segundo.motor == "deepfilternet" and not segundo.do_cache

    terceiro = optimize_audio(entrada, tmp_path / "c.wav", params, settings=settings)
    assert terceiro.do_cache and terceiro.motor == "deepfilternet"


@requires_ffmpeg
def test_cache_is_kept_when_the_engine_is_already_the_best(tmp_path, monkeypatch):
    """Sem motor melhor à vista, o cache vale — inclusive com o denoise desligado."""
    entrada = wav_ruidoso(tmp_path / "ruido.wav")
    settings = S(cache_dir=tmp_path / "cache")
    for params in (AudioParams(motor="afftdn"), AudioParams(aggressiveness=0)):
        optimize_audio(entrada, tmp_path / "a.wav", params, settings=settings)
        segundo = optimize_audio(entrada, tmp_path / "b.wav", params, settings=settings)
        assert segundo.do_cache, params


@requires_ffmpeg
def test_use_cache_false_always_reprocesses(tmp_path):
    entrada = wav_ruidoso(tmp_path / "ruido.wav")
    params = AudioParams(motor="afftdn")
    settings = S(cache_dir=tmp_path / "cache")
    optimize_audio(entrada, tmp_path / "a.wav", params, settings=settings)
    r = optimize_audio(entrada, tmp_path / "b.wav", params, settings=settings, use_cache=False)
    assert not r.do_cache


@requires_ffmpeg
def test_chain_accepts_a_video_and_can_skip_steps(tmp_path, video_dir):
    r = optimize_audio(
        video_dir / "1.mp4",
        tmp_path / "video.wav",
        AudioParams(motor="afftdn", highpass_hz=0, normalizar=False),
        settings=S(cache_dir=tmp_path / "cache"),
    )
    dados, taxa = metrics.read_wav(r.caminho)
    assert taxa == 48_000 and len(dados) > 1000


@requires_ffmpeg
def test_highpass_removes_the_rumble(tmp_path):
    """80 Hz para baixo é rumble/ar/DC: tem que sumir, e a voz tem que ficar."""
    taxa = 48_000
    t_s = np.arange(taxa * 2) / taxa
    grave = 0.3 * np.sin(2 * np.pi * 40 * t_s)  # rumble
    voz = 0.3 * np.sin(2 * np.pi * 300 * t_s)  # "voz"
    entrada = tmp_path / "com_rumble.wav"
    with wave.open(str(entrada), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(((grave + voz) * 32000).astype(np.int16).tobytes())

    from src.audio.optimize import _highpass

    def energia(arquivo: Path, a: float, b: float) -> float:
        dados, _ = metrics.read_wav(arquivo)
        espectro = np.abs(np.fft.rfft(dados * np.hanning(len(dados))))
        freqs = np.fft.rfftfreq(len(dados), 1 / taxa)
        return float(espectro[(freqs >= a) & (freqs < b)].sum())

    saida = _highpass(entrada, tmp_path / "sem_rumble.wav", 80)
    # o filtro do FFmpeg é de 12 dB por oitava: a 40 Hz (uma oitava abaixo) espera-se ~12 dB
    queda_db = 20 * np.log10(energia(saida, 30, 50) / energia(entrada, 30, 50))
    voz_db = 20 * np.log10(energia(saida, 280, 320) / energia(entrada, 280, 320))
    assert queda_db < -8  # o rumble cai
    assert voz_db == pytest.approx(0, abs=1)  # a voz passa intacta


@requires_ffmpeg
def test_without_normalization_the_volume_is_untouched(tmp_path):
    entrada = wav_ruidoso(tmp_path / "baixo.wav", ganho=0.05, ruido_db=-60)
    r = optimize_audio(
        entrada,
        tmp_path / "saida.wav",
        AudioParams(motor="afftdn", normalizar=False),
        settings=S(cache_dir=tmp_path / "cache"),
    )
    assert r.lufs_depois == pytest.approx(r.lufs_antes, abs=2)  # continua baixo
    assert r.ruido_removido_db > 3  # sem ganho no fim, o piso de ruído cai de verdade


@requires_ffmpeg
def test_aggressiveness_can_be_passed_directly(tmp_path):
    entrada = wav_ruidoso(tmp_path / "ruido.wav")
    r = optimize_audio(entrada, tmp_path / "s.wav", 0.0, settings=S(cache_dir=tmp_path / "c"))
    assert r.motor == "nenhum"


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        optimize_audio(tmp_path / "nao_existe.wav", tmp_path / "x.wav", settings=S())


# ------------------------------------------------------------------ com o DeepFilterNet


@pytest.mark.integration
@requires_ffmpeg
def test_real_speech_gets_cleaner_without_losing_the_voice(tmp_path):
    """Critério de aceite: ruidoso sai limpo e no volume certo; limpo não piora."""
    from src.config import SAMPLES_DIR

    fontes = sorted(
        p for p in SAMPLES_DIR.iterdir() if p.suffix.lower() in {".mp4", ".wav", ".mp3"}
    )
    if not fontes:
        pytest.skip("coloque um vídeo com voz em samples/ (veja testes-pendentes.md)")

    voz = metrics.to_wav(fontes[0], tmp_path / "voz.wav")
    dados, taxa = metrics.read_wav(voz)
    dados = dados[: taxa * 15]
    rng = np.random.default_rng(5)
    ruidoso = tmp_path / "voz_ruidosa.wav"
    with wave.open(str(ruidoso), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        mistura = np.clip(dados + rng.normal(0, 10 ** (-33 / 20), len(dados)), -1, 1)
        w.writeframes((mistura * 32767).astype(np.int16).tobytes())

    settings = S(cache_dir=tmp_path / "cache")
    sujo = optimize_audio(ruidoso, tmp_path / "limpo.wav", settings=settings)
    assert sujo.motor == "deepfilternet"
    assert sujo.ganho_snr_db > 15
    assert sujo.lufs_depois == pytest.approx(-16, abs=1.5)

    # áudio que já estava bom: o volume vai ao alvo e a fala não some
    limpo = optimize_audio(voz, tmp_path / "limpo2.wav", settings=settings)
    assert limpo.lufs_depois == pytest.approx(-16, abs=1.5)
    assert limpo.depois.snr >= limpo.antes.snr
