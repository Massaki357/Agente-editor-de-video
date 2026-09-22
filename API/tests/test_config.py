from pathlib import Path

import pytest

from src.config import _ENV_FIELDS, PROJECT_ROOT, Settings, load_settings


@pytest.fixture
def clean_env(monkeypatch):
    for env in _ENV_FIELDS:
        monkeypatch.delenv(env, raising=False)
    return monkeypatch


def test_defaults_without_env_file(clean_env, tmp_path):
    s = load_settings(tmp_path / "nao_existe.env")
    assert s.llm_model == "openai:gpt-5-mini"
    assert s.whisper_device == "auto"
    assert s.openai_api_key is None
    assert s.cache_dir == PROJECT_ROOT / ".cache"
    assert (s.output_width, s.output_height, s.output_fps) == (1080, 1920, 30)


def test_reads_env_file_with_inline_comments(clean_env, tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "LLM_MODEL=anthropic:claude-haiku-4-5   # comentario\n"
        "ANTHROPIC_API_KEY=sk-test\n"
        "OPENAI_API_KEY=                # vazia\n"
        "WHISPER_DEVICE=CPU\n"
        "CACHE_DIR=meu_cache\n",
        encoding="utf-8",
    )
    s = load_settings(env)
    assert s.llm_model == "anthropic:claude-haiku-4-5"
    assert s.llm_provider == "anthropic"
    assert s.api_key_for_provider("anthropic").get_secret_value() == "sk-test"
    assert s.openai_api_key is None
    assert s.whisper_device == "cpu"
    assert s.cache_dir == PROJECT_ROOT / "meu_cache"


def test_env_example_parses_to_empty_keys(clean_env):
    s = load_settings(PROJECT_ROOT / ".env.example")
    assert s.anthropic_api_key is None
    assert s.pexels_api_key is None
    assert s.whisper_model == "large-v3-turbo"


def test_process_env_overrides_env_file(clean_env, tmp_path):
    env = tmp_path / ".env"
    env.write_text("WHISPER_MODEL=small\n", encoding="utf-8")
    clean_env.setenv("WHISPER_MODEL", "medium")
    assert load_settings(env).whisper_model == "medium"


def test_invalid_whisper_device_is_rejected(clean_env, tmp_path):
    clean_env.setenv("WHISPER_DEVICE", "tpu")
    with pytest.raises(ValueError):
        load_settings(tmp_path / "x.env")


def test_absolute_cache_dir_is_kept(clean_env, tmp_path):
    clean_env.setenv("CACHE_DIR", str(tmp_path))
    assert load_settings(tmp_path / "x.env").cache_dir == Path(tmp_path)


def test_secret_is_not_leaked_in_repr(clean_env, tmp_path):
    clean_env.setenv("OPENAI_API_KEY", "sk-super-secreta")
    assert "sk-super-secreta" not in repr(load_settings(tmp_path / "x.env"))


def test_empty_key_in_api_env_does_not_hide_root_env(clean_env, tmp_path, monkeypatch):
    import src.config as config

    api_env, root_env = tmp_path / "api.env", tmp_path / "root.env"
    api_env.write_text("OPENAI_API_KEY=\nWHISPER_MODEL=small\n", encoding="utf-8")
    root_env.write_text("OPENAI_API_KEY=sk-raiz\nWHISPER_MODEL=medium\n", encoding="utf-8")
    monkeypatch.setattr(config, "default_env_files", lambda: [api_env, root_env])
    s = config.load_settings()
    assert s.openai_api_key.get_secret_value() == "sk-raiz"  # vazio em API/.env não esconde
    assert s.whisper_model == "small"  # API/.env tem precedência quando preenchido


def test_available_models_need_the_provider_key():
    s = Settings(llm_model="openai:gpt-5-mini", openai_api_key="k")
    assert s.available_llm_models() == ["openai:gpt-5-mini", "openai:gpt-5"]
    com_anthropic = s.model_copy(update={"anthropic_api_key": "k2"})
    assert "anthropic:claude-sonnet-5" in com_anthropic.available_llm_models()
    # o modelo do .env aparece mesmo fora da lista (ou sem chave)
    outro = Settings(llm_model="openai:gpt-6-turbo")
    assert outro.available_llm_models() == ["openai:gpt-6-turbo"]


def test_for_model_only_changes_the_model():
    s = Settings(llm_model="openai:gpt-5-mini", openai_api_key="k")
    assert s.for_model(None) is s and s.for_model("openai:gpt-5-mini") is s
    outro = s.for_model("anthropic:claude-sonnet-5")
    assert outro.llm_model == "anthropic:claude-sonnet-5"
    assert outro.openai_api_key == s.openai_api_key and outro.cache_dir == s.cache_dir
