"""Configurações tipadas lidas do `.env` (e das variáveis de ambiente do processo)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr, field_validator

# PROJECT_ROOT = pasta API/ (backend); REPO_ROOT = raiz do repositório (API/, frontend/,
# samples/, output/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent
SAMPLES_DIR = REPO_ROOT / "samples"
OUTPUT_DIR = REPO_ROOT / "output"

WhisperDevice = Literal["auto", "cuda", "cpu"]


# Modelos oferecidos na interface (o `.env` pode usar qualquer outro que o LangChain
# aceite, no formato `provedor:modelo`).
LLM_MODELS = (
    "openai:gpt-5-mini",
    "openai:gpt-5",
    "anthropic:claude-sonnet-5",
    "anthropic:claude-haiku-4-5",
)


class Settings(BaseModel):
    llm_model: str = "openai:gpt-5-mini"
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    whisper_model: str = "large-v3-turbo"
    whisper_device: WhisperDevice = "auto"
    pexels_api_key: SecretStr | None = None
    pixabay_api_key: SecretStr | None = None
    cache_dir: Path = Field(default=PROJECT_ROOT / ".cache")
    log_dir: Path = Field(default=PROJECT_ROOT / "logs")
    data_dir: Path = Field(
        default=PROJECT_ROOT / "data", description="projetos, uploads e vídeos da API"
    )
    log_level: str = "INFO"

    # Formato final (decisão fixa).
    output_width: int = 1080
    output_height: int = 1920
    output_fps: int = 30
    output_sample_rate: int = 48_000

    @field_validator(
        "openai_api_key", "anthropic_api_key", "pexels_api_key", "pixabay_api_key", mode="before"
    )
    @classmethod
    def _empty_key_is_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("whisper_device", mode="before")
    @classmethod
    def _normalize_device(cls, v: object) -> object:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("cache_dir", "log_dir", "data_dir", mode="after")
    @classmethod
    def _resolve_relative(cls, v: Path) -> Path:
        return v if v.is_absolute() else PROJECT_ROOT / v

    @property
    def llm_provider(self) -> str:
        """Prefixo `provedor:` de `LLM_MODEL` (ex.: `openai`)."""
        return self.llm_model.split(":", 1)[0] if ":" in self.llm_model else ""

    def api_key_for_provider(self, provider: str) -> SecretStr | None:
        return {"openai": self.openai_api_key, "anthropic": self.anthropic_api_key}.get(provider)

    def available_llm_models(self) -> list[str]:
        """Modelos da lista cujo provedor tem chave, mais o do `.env`, sem repetir."""
        disponiveis = [
            m for m in LLM_MODELS if self.api_key_for_provider(m.split(":", 1)[0]) is not None
        ]
        if self.llm_model not in disponiveis:
            disponiveis.insert(0, self.llm_model)
        return disponiveis

    def for_model(self, llm_model: str | None) -> Settings:
        """Estas configurações com outro modelo de LLM (escolhido na interface)."""
        if not llm_model or llm_model == self.llm_model:
            return self
        return self.model_copy(update={"llm_model": llm_model})


_ENV_FIELDS = {
    "LLM_MODEL": "llm_model",
    "OPENAI_API_KEY": "openai_api_key",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "WHISPER_MODEL": "whisper_model",
    "WHISPER_DEVICE": "whisper_device",
    "PEXELS_API_KEY": "pexels_api_key",
    "PIXABAY_API_KEY": "pixabay_api_key",
    "CACHE_DIR": "cache_dir",
    "LOG_DIR": "log_dir",
    "DATA_DIR": "data_dir",
    "LOG_LEVEL": "log_level",
}


def _clean(value: str | None) -> str:
    # python-dotenv lê `KEY=      # comentário` como o valor "# comentário".
    value = (value or "").strip()
    return "" if value.startswith("#") else value


def default_env_files() -> list[Path]:
    """`API/.env` e, como alternativa, o `.env` da raiz do repositório."""
    return [PROJECT_ROOT / ".env", REPO_ROOT / ".env"]


def load_settings(env_file: Path | None = None) -> Settings:
    """Monta o `Settings` a partir do `.env`; variáveis do processo têm precedência.

    Sem `env_file`, lê `API/.env` e o `.env` da raiz (o primeiro tem precedência).
    """
    file_values: dict[str, str | None] = {}
    for path in reversed([env_file] if env_file else default_env_files()):
        # valor vazio (ex.: `KEY=` copiado do .env.example) não esconde o do outro arquivo
        file_values.update({k: v for k, v in dotenv_values(path).items() if _clean(v)})
    values = {}
    for env, field in _ENV_FIELDS.items():
        raw = os.environ.get(env, file_values.get(env))
        if raw is not None:
            values[field] = _clean(raw)
    return Settings(**{k: v for k, v in values.items() if v or k.endswith("api_key")})


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
