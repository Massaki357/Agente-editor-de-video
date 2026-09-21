"""Testes da camada LLM com o modelo mockado (nenhuma chamada de rede)."""

from __future__ import annotations

import json
import logging

import pytest
from pydantic import BaseModel, SecretStr

from src.config import Settings
from src.llm import client


class Saida(BaseModel):
    numero: int
    palavra: str


class FakeRaw:
    def __init__(self, input_tokens: int = 10, output_tokens: int = 5):
        self.usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }


def ok(numero: int = 7, palavra: str = "ação", **usage) -> dict:
    return {
        "raw": FakeRaw(**usage),
        "parsed": Saida(numero=numero, palavra=palavra),
        "parsing_error": None,
    }


def invalid() -> dict:
    return {"raw": FakeRaw(), "parsed": None, "parsing_error": ValueError("json quebrado")}


class ApiError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class BadRequestError(ApiError):
    def __init__(self, message: str):
        super().__init__(message, 400)


class AuthenticationError(ApiError):
    def __init__(self, message: str = "Incorrect API key provided"):
        super().__init__(message, 401)


class APIConnectionError(Exception):
    pass


class FakeFactory:
    """Substitui `client._make_model`. Cada `invoke` consome o próximo item de `responses`:
    dict → devolvido; exceção → lançada; callable(temperature) → resultado dele."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.made: list[tuple[str, float | None]] = []
        self.messages: list = []
        self.schemas: list = []

    def __call__(self, settings, temperature):
        self.made.append((settings.llm_model, temperature))
        factory = self

        class Structured:
            def invoke(self, messages):
                factory.messages.append(messages)
                item = factory.responses.pop(0)
                if callable(item) and not isinstance(item, type):
                    item = item(temperature)
                if isinstance(item, BaseException):
                    raise item
                return item

        class Model:
            def with_structured_output(self, schema, include_raw=False):
                factory.schemas.append((schema, include_raw))
                return Structured()

        return Model()

    @property
    def calls(self) -> int:
        return len(self.messages)


@pytest.fixture
def prompts(tmp_path, monkeypatch):
    (tmp_path / "teste.md").write_text("Você é um teste. Responda só JSON.", encoding="utf-8")
    monkeypatch.setattr(client, "PROMPTS_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(client, "_sleep", sleeps.append)
    client.reset_usage()
    client._no_temperature.clear()
    yield sleeps
    client.reset_usage()
    client._no_temperature.clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(llm_model="openai:gpt-5-mini", openai_api_key=SecretStr("sk-teste"))


def use(monkeypatch, responses) -> FakeFactory:
    fake = FakeFactory(responses)
    monkeypatch.setattr(client, "_make_model", fake)
    return fake


def test_sucesso_devolve_instancia_e_monta_mensagens(prompts, settings, monkeypatch):
    fake = use(monkeypatch, [ok()])
    out = client.run_structured("teste", {"texto": "ação é você"}, Saida, settings=settings)

    assert out == Saida(numero=7, palavra="ação")
    assert fake.schemas == [(Saida, True)]
    system, human = fake.messages[0]
    assert system.content == "Você é um teste. Responda só JSON."
    assert human.content == '{"texto": "ação é você"}'
    assert json.loads(human.content) == {"texto": "ação é você"}
    assert fake.made == [("openai:gpt-5-mini", 0.0)]


def test_input_str_passa_direto(prompts, settings, monkeypatch):
    fake = use(monkeypatch, [ok()])
    client.run_structured("teste", "0 olá 0.00 0.40", Saida, settings=settings)
    assert fake.messages[0][1].content == "0 olá 0.00 0.40"


def test_input_lista_de_pydantic_vira_json(prompts, settings, monkeypatch):
    fake = use(monkeypatch, [ok()])
    client.run_structured("teste", [Saida(numero=1, palavra="é")], Saida, settings=settings)
    assert fake.messages[0][1].content == '[{"numero": 1, "palavra": "é"}]'


def test_prompt_inexistente_gera_llmerror(prompts, settings, monkeypatch):
    fake = use(monkeypatch, [ok()])
    with pytest.raises(client.LLMError, match="não encontrado"):
        client.run_structured("nao_existe", {}, Saida, settings=settings)
    assert fake.calls == 0


def test_schema_invalido_duas_vezes_depois_valido(prompts, settings, monkeypatch, clean_state):
    fake = use(
        monkeypatch, [invalid(), {"raw": FakeRaw(), "parsed": None, "parsing_error": None}, ok()]
    )
    out = client.run_structured("teste", {}, Saida, settings=settings, backoff=1.0)
    assert out.numero == 7
    assert fake.calls == 3
    assert clean_state == [1.0, 2.0]  # backoff * 2**n


def test_dict_valido_e_validado_pelo_schema(prompts, settings, monkeypatch):
    use(
        monkeypatch,
        [{"raw": FakeRaw(), "parsed": {"numero": 3, "palavra": "x"}, "parsing_error": None}],
    )
    assert client.run_structured("teste", {}, Saida, settings=settings) == Saida(
        numero=3, palavra="x"
    )


def test_dict_fora_do_schema_conta_como_invalido(prompts, settings, monkeypatch):
    fake = use(
        monkeypatch, [{"raw": FakeRaw(), "parsed": {"numero": "abc"}, "parsing_error": None}, ok()]
    )
    client.run_structured("teste", {}, Saida, settings=settings)
    assert fake.calls == 2


def test_schema_sempre_invalido_gera_llmerror(prompts, settings, monkeypatch, caplog):
    fake = use(monkeypatch, [invalid(), invalid(), invalid()])
    with caplog.at_level(logging.WARNING), pytest.raises(client.LLMError, match="3 tentativas"):
        client.run_structured("teste", {}, Saida, settings=settings)
    assert fake.calls == 3
    assert "fora do schema" in caplog.text


def test_erro_de_rede_transitorio_faz_retry(prompts, settings, monkeypatch, clean_state):
    fake = use(
        monkeypatch,
        [
            APIConnectionError("Connection error."),
            TimeoutError("timeout"),
            ApiError("overloaded", 529),
            ok(),
        ],
    )
    out = client.run_structured("teste", {}, Saida, settings=settings, attempts=4, backoff=0.5)
    assert out.palavra == "ação"
    assert fake.calls == 4
    assert clean_state == [0.5, 1.0, 2.0]


def test_rate_limit_esgota_tentativas(prompts, settings, monkeypatch):
    fake = use(monkeypatch, [ApiError("rate limit", 429)] * 2)
    with pytest.raises(client.LLMError) as info:
        client.run_structured("teste", {}, Saida, settings=settings, attempts=2)
    assert fake.calls == 2
    assert isinstance(info.value.__cause__, ApiError)


def test_erro_de_autenticacao_sem_retry(prompts, settings, monkeypatch, clean_state):
    fake = use(monkeypatch, [AuthenticationError(), ok()])
    with pytest.raises(client.LLMError, match="autenticação"):
        client.run_structured("teste", {}, Saida, settings=settings)
    assert fake.calls == 1
    assert clean_state == []


def test_chave_ausente_gera_llmerror_sem_chamar(prompts, monkeypatch):
    fake = use(monkeypatch, [ok()])
    settings = Settings(llm_model="anthropic:claude-haiku-4-5", anthropic_api_key=None)
    with pytest.raises(client.LLMError, match="ANTHROPIC_API_KEY"):
        client.run_structured("teste", {}, Saida, settings=settings)
    assert fake.calls == 0


def test_erro_nao_recuperavel_sem_retry(prompts, settings, monkeypatch):
    fake = use(monkeypatch, [ApiError("model not found", 404), ok()])
    with pytest.raises(client.LLMError, match="model not found"):
        client.run_structured("teste", {}, Saida, settings=settings)
    assert fake.calls == 1


def test_fallback_de_temperatura_memorizado(prompts, settings, monkeypatch, caplog):
    def responde(temperature):
        if temperature is not None:
            return BadRequestError("Unsupported value: 'temperature' does not support 0")
        return ok()

    fake = use(monkeypatch, [responde, responde, responde])
    with caplog.at_level(logging.WARNING):
        client.run_structured("teste", {}, Saida, settings=settings)
        client.run_structured("teste", {}, Saida, settings=settings)

    # 1ª chamada: com temperatura (recusada) e sem; 2ª: direto sem temperatura.
    assert fake.made == [
        ("openai:gpt-5-mini", 0.0),
        ("openai:gpt-5-mini", None),
        ("openai:gpt-5-mini", None),
    ]
    assert caplog.text.count("não aceita temperature") == 1


def test_bad_request_sem_temperature_nao_faz_fallback(prompts, settings, monkeypatch):
    fake = use(monkeypatch, [BadRequestError("context length exceeded"), ok()])
    with pytest.raises(client.LLMError):
        client.run_structured("teste", {}, Saida, settings=settings)
    assert fake.made == [("openai:gpt-5-mini", 0.0)]


def test_trocar_llm_model_so_muda_id_do_modelo(prompts, monkeypatch):
    captured: list[tuple[str, dict]] = []

    class Model:
        def with_structured_output(self, schema, include_raw=False):
            return self

        def invoke(self, messages):
            return ok()

    def fake_init(model_id, **kwargs):
        captured.append((model_id, kwargs))
        return Model()

    monkeypatch.setattr(client, "init_chat_model", fake_init)
    for model_id, kwargs in [
        ("openai:gpt-5-mini", {"openai_api_key": SecretStr("sk-o")}),
        ("anthropic:claude-haiku-4-5", {"anthropic_api_key": SecretStr("sk-a")}),
    ]:
        client.run_structured("teste", {}, Saida, settings=Settings(llm_model=model_id, **kwargs))

    assert [c[0] for c in captured] == ["openai:gpt-5-mini", "anthropic:claude-haiku-4-5"]
    assert captured[0][1]["api_key"] == "sk-o"
    assert captured[1][1]["api_key"] == "sk-a"
    for _, kwargs in captured:
        assert kwargs["temperature"] == 0
        assert kwargs["max_retries"] == client.PROVIDER_MAX_RETRIES
        assert kwargs["timeout"] == client.TIMEOUT_S


def test_acumulador_de_tokens(prompts, settings, monkeypatch, caplog):
    use(
        monkeypatch,
        [invalid(), ok(input_tokens=100, output_tokens=20), ok(input_tokens=50, output_tokens=5)],
    )
    with caplog.at_level(logging.INFO):
        client.run_structured("teste", {}, Saida, settings=settings)
        client.run_structured("teste", {}, Saida, settings=settings)

    totals = client.usage_totals()
    # A tentativa inválida também gastou tokens (10/5 do FakeRaw padrão).
    assert totals == {"openai:gpt-5-mini": {"input_tokens": 160, "output_tokens": 30, "calls": 3}}
    assert "prompt=teste modelo=openai:gpt-5-mini tentativas=2" in caplog.text
    assert "entrada=110 saída=25" in caplog.text

    client.reset_usage()
    assert client.usage_totals() == {}
