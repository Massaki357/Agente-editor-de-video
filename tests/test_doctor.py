import pytest

import src.doctor as doctor
from src.config import Settings


def test_missing_ffmpeg_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    checks = doctor.check_ffmpeg()
    assert [c.status for c in checks] == [doctor.Status.FAIL, doctor.Status.FAIL]


def test_missing_llm_key_is_fail_and_stock_keys_are_warnings():
    checks = {c.name: c.status for c in doctor.check_keys(Settings(llm_model="openai:gpt-5-mini"))}
    assert checks["OPENAI_API_KEY"] is doctor.Status.FAIL
    assert checks["PEXELS_API_KEY"] is doctor.Status.WARN
    assert checks["PIXABAY_API_KEY"] is doctor.Status.WARN


def test_a_crashing_check_does_not_break_the_run(monkeypatch):
    def boom():
        raise RuntimeError("quebrou")

    monkeypatch.setattr(doctor, "check_python", boom)
    checks = doctor.run_checks(Settings())
    assert any(c.name == "Python" and c.status is doctor.Status.FAIL for c in checks)
    assert len(checks) > 1


@pytest.fixture
def no_logging(monkeypatch):
    monkeypatch.setattr(doctor, "setup_logging", lambda *a, **k: None)


def test_main_prints_report_and_exits_zero(capsys, monkeypatch, tmp_path, no_logging):
    monkeypatch.setattr(doctor, "get_settings", lambda: Settings(cache_dir=tmp_path))
    assert doctor.main([]) == 0
    out = capsys.readouterr().out
    assert "ffmpeg" in out and "itens:" in out


def test_invalid_config_is_reported_not_raised(capsys, monkeypatch, no_logging):
    def invalid():
        return Settings(whisper_device="gpu")

    monkeypatch.setattr(doctor, "get_settings", invalid)
    assert doctor.main([]) == 0
    assert doctor.main(["--strict"]) == 1
    out = capsys.readouterr().out
    assert "[FALTA] Configuração (.env)" in out and "WHISPER_DEVICE" in out


def test_invalid_log_level_falls_back_to_info(tmp_path, monkeypatch):
    import logging

    import src.logging_setup as ls

    root = logging.getLogger()
    monkeypatch.setattr(ls, "_configured", False)
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", root.level)
    ls.setup_logging(tmp_path, "VERBOSE")
    assert root.level == logging.INFO
    for h in root.handlers:
        h.close()
