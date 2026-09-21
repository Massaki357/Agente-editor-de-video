"""Único ponto de acesso ao LLM (LangChain).

O resto do código chama só `run_structured(prompt_name, input, schema)`. Nada de agents, chains
ou memória: `init_chat_model` + `.with_structured_output(schema, include_raw=True)`.

Falhas:
- rede/timeout/429/5xx e resposta fora do schema → retry com backoff exponencial;
- chave ausente, autenticação, modelo inexistente e outros erros do provedor → `LLMError` imediato;
- modelo que recusa `temperature` → refaz sem o parâmetro e memoriza a decisão por modelo.

Quem chama trata `LLMError` caindo no comportamento sem LLM (nunca quebra o pipeline).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from src.config import Settings, get_settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
TIMEOUT_S = 120.0
PROVIDER_MAX_RETRIES = 2  # retries internos do SDK (rede/429/5xx) antes de chegar aqui
TEMPERATURE = 0.0

# Provedores cujas chaves vêm de `Settings`; para outros, o pacote do provedor lê o ambiente.
_KNOWN_PROVIDERS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

_TRANSIENT_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InternalServerError",
    "ServiceUnavailableError",
    "OverloadedError",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "ReadError",
    "RemoteProtocolError",
}
_AUTH_NAMES = {"AuthenticationError", "PermissionDeniedError"}


class LLMError(RuntimeError):
    """Falha definitiva do LLM. Quem chama deve cair no comportamento sem LLM."""


# ---------------------------------------------------------------- estado em memória
_lock = threading.Lock()
_no_temperature: set[str] = set()  # modelos que recusaram `temperature`
_usage: dict[str, dict[str, int]] = {}


def usage_totals() -> dict[str, dict[str, int]]:
    """Tokens acumulados por modelo: `{modelo: {"input_tokens", "output_tokens", "calls"}}`."""
    with _lock:
        return {model: dict(v) for model, v in _usage.items()}


def reset_usage() -> None:
    with _lock:
        _usage.clear()


def _add_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    with _lock:
        entry = _usage.setdefault(model, {"input_tokens": 0, "output_tokens": 0, "calls": 0})
        entry["input_tokens"] += input_tokens
        entry["output_tokens"] += output_tokens
        entry["calls"] += 1


# ---------------------------------------------------------------- modelo
def _make_model(settings: Settings, temperature: float | None) -> Any:
    """Cria o chat model. Ponto de mock dos testes."""
    kwargs: dict[str, Any] = {"timeout": TIMEOUT_S, "max_retries": PROVIDER_MAX_RETRIES}
    if temperature is not None:
        kwargs["temperature"] = temperature
    key = settings.api_key_for_provider(settings.llm_provider)
    if key is not None:
        kwargs["api_key"] = key.get_secret_value()
    return init_chat_model(settings.llm_model, **kwargs)


def _check_key(settings: Settings) -> None:
    provider = settings.llm_provider
    env = _KNOWN_PROVIDERS.get(provider)
    if env and settings.api_key_for_provider(provider) is None:
        raise LLMError(
            f"Chave do provedor '{provider}' ausente: preencha {env} no .env "
            f"(LLM_MODEL={settings.llm_model})."
        )


# ---------------------------------------------------------------- classificação de erros
def _status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    return code if isinstance(code, int) else None


def _is_auth_error(exc: BaseException) -> bool:
    return type(exc).__name__ in _AUTH_NAMES or _status_code(exc) in (401, 403)


def _is_temperature_error(exc: BaseException) -> bool:
    bad_request = type(exc).__name__ == "BadRequestError" or _status_code(exc) in (400, 422)
    return bad_request and "temperature" in str(exc).lower()


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    if any(cls.__name__ in _TRANSIENT_NAMES for cls in type(exc).__mro__):
        return True
    code = _status_code(exc)
    return code is not None and (code in (408, 409, 429) or code >= 500)


class _SchemaError(Exception):
    """Resposta do modelo fora do schema (vale retry)."""


# ---------------------------------------------------------------- helpers
def _load_prompt(prompt_name: str) -> str:
    path = PROMPTS_DIR / f"{prompt_name}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LLMError(f"Prompt '{prompt_name}' não encontrado em {path}") from exc


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Tipo não serializável para o LLM: {type(value).__name__}")


def _payload(input: Any) -> str:
    if isinstance(input, str):
        return input
    return json.dumps(input, ensure_ascii=False, default=_json_default)


def _usage_of(raw: Any) -> tuple[int, int] | None:
    meta = getattr(raw, "usage_metadata", None)
    if not meta:
        return None
    get = meta.get if isinstance(meta, dict) else lambda k, d=0: getattr(meta, k, d)
    return int(get("input_tokens", 0) or 0), int(get("output_tokens", 0) or 0)


def _parse(result: Any, schema: type[T]) -> T:
    """Extrai a instância do envelope `{raw, parsed, parsing_error}` (ou do valor direto)."""
    if isinstance(result, dict) and "parsed" in result:
        if result.get("parsing_error") is not None:
            raise _SchemaError(str(result["parsing_error"]))
        parsed = result.get("parsed")
    else:
        parsed = result
    if parsed is None:
        raise _SchemaError("modelo não devolveu saída estruturada (parsed=None)")
    if isinstance(parsed, schema):
        return parsed
    try:
        data = parsed.model_dump() if isinstance(parsed, BaseModel) else parsed
        return schema.model_validate(data)
    except ValidationError as exc:
        raise _SchemaError(str(exc)) from exc


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


# ---------------------------------------------------------------- API pública
def run_structured(
    prompt_name: str,
    input: Any,
    schema: type[T],
    *,
    settings: Settings | None = None,
    attempts: int = 3,
    backoff: float = 1.0,
) -> T:
    """Roda o prompt `prompts/{prompt_name}.md` com `input` e devolve uma instância de `schema`.

    Lança `LLMError` em falha definitiva (após `attempts` tentativas, ou imediatamente em erro
    de autenticação/chave/prompt/erro não recuperável do provedor).
    """
    settings = settings or get_settings()
    model_id = settings.llm_model
    _check_key(settings)
    messages = [SystemMessage(_load_prompt(prompt_name)), HumanMessage(_payload(input))]

    attempts = max(1, attempts)
    started = time.perf_counter()
    in_tok = out_tok = 0
    got_usage = False
    last_error: BaseException | None = None

    def finish(status: str, tries: int) -> None:
        tokens = f"entrada={in_tok} saída={out_tok}" if got_usage else "n/d"
        log.info(
            "LLM prompt=%s modelo=%s tentativas=%d tempo=%.2fs tokens %s status=%s",
            prompt_name,
            model_id,
            tries,
            time.perf_counter() - started,
            tokens,
            status,
        )

    for attempt in range(1, attempts + 1):
        try:
            result = _invoke(settings, schema, messages)
            usage = _usage_of(result.get("raw") if isinstance(result, dict) else None)
            if usage:
                got_usage = True
                in_tok += usage[0]
                out_tok += usage[1]
                _add_usage(model_id, *usage)
            parsed = _parse(result, schema)
            finish("ok", attempt)
            return parsed
        except LLMError:
            finish("erro", attempt)
            raise
        except (_SchemaError, ValidationError, OutputParserException) as exc:
            last_error = exc
            log.warning(
                "LLM %s: resposta fora do schema (tentativa %d): %s",
                prompt_name,
                attempt,
                _short(exc),
            )
        except Exception as exc:
            if _is_auth_error(exc):
                finish("erro", attempt)
                raise LLMError(
                    f"Falha de autenticação no provedor de '{model_id}': confira a chave no .env. "
                    f"({_short(exc)})"
                ) from exc
            if not _is_transient(exc):
                finish("erro", attempt)
                raise LLMError(f"Erro do LLM ({model_id}): {_short(exc)}") from exc
            last_error = exc
            log.warning(
                "LLM %s: falha transitória (tentativa %d): %s", prompt_name, attempt, _short(exc)
            )
        if attempt < attempts:
            _sleep(backoff * 2 ** (attempt - 1))

    finish("falhou", attempts)
    raise LLMError(
        f"LLM falhou após {attempts} tentativas ({prompt_name}, {model_id}): {_short(last_error)}"
    ) from last_error


def _invoke(settings: Settings, schema: type[BaseModel], messages: list) -> Any:
    """Uma chamada; se o modelo recusar `temperature`, refaz sem ela e memoriza."""
    model_id = settings.llm_model
    use_temperature = model_id not in _no_temperature
    try:
        model = _make_model(settings, TEMPERATURE if use_temperature else None)
        return model.with_structured_output(schema, include_raw=True).invoke(messages)
    except Exception as exc:
        if not (use_temperature and _is_temperature_error(exc)):
            raise
        with _lock:
            _no_temperature.add(model_id)
        log.warning(
            "Modelo %s não aceita temperature=%s; seguindo sem o parâmetro.", model_id, TEMPERATURE
        )
        model = _make_model(settings, None)
        return model.with_structured_output(schema, include_raw=True).invoke(messages)


def _short(exc: BaseException | None, limit: int = 300) -> str:
    if exc is None:
        return "sem detalhes"
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= limit else text[:limit] + "..."
