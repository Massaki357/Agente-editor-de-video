---
name: langchain-structured
description: Como a camada LLM do editor de vídeos usa LangChain (init_chat_model + with_structured_output com Pydantic), com retry, validação e mocks nos testes. Use ao mexer em src/llm/, prompts, schemas ou em qualquer etapa que chame o LLM (4, 8, 9).
---

# Camada LLM (src/llm)

## Regras fixas
- Só `src/llm/client.py` importa LangChain (um hook bloqueia imports em outros arquivos de `src/`).
- Nada de agents, chains legadas ou memória. Só `init_chat_model` e `.with_structured_output()`.
- `init_chat_model` vem do pacote `langchain` (`from langchain.chat_models import init_chat_model`); mensagens vêm de `langchain_core.messages`.
- Prompts ficam em `src/llm/prompts/<nome>.md`. Carregue-os com `Path(...).read_text(encoding="utf-8")`.
- O LLM só recebe texto: palavras com `indice`, `texto`, `inicio`, `fim`. Nunca coordenadas; posição e zoom saem de geometria no código.

## API atual (conferida no Context7, set/2026)
```python
from langchain.chat_models import init_chat_model

model = init_chat_model(settings.llm_model, temperature=0, timeout=120, max_retries=2,
                        api_key=key.get_secret_value())  # "openai:gpt-5-mini", "anthropic:..."
structured = model.with_structured_output(Schema)       # Schema = modelo Pydantic
result: Schema = structured.invoke([SystemMessage(prompt), HumanMessage(payload_json)])
```
- A string `provedor:modelo` escolhe o pacote (`langchain-openai`, `langchain-anthropic`). Trocar de provedor = mudar `LLM_MODEL` no `.env`.
- Alguns modelos de raciocínio da OpenAI não aceitam `temperature`. Se o provedor recusar, tente de novo sem o parâmetro e registre no log.
- `max_retries` do modelo cobre rede, 429 e 5xx. Falha de schema (`ValidationError`, `OutputParserException`) é tratada por um retry com backoff próprio em `run_structured`.
- Passe a chave explicitamente vinda de `Settings`, sem depender de `os.environ`.

## Contrato de `run_structured(prompt_name, input, schema)`
1. Monta as mensagens a partir do prompt e de `json.dumps(input, ensure_ascii=False)`.
2. Chama o modelo com retry e backoff exponencial (ex.: 3 tentativas: 1 s, 2 s, 4 s).
3. Devolve a instância Pydantic, ou lança `LLMError`. Quem chama decide o fallback: **resposta inválida nunca quebra o pipeline**, cai no comportamento sem LLM e loga um aviso.
4. Registra no log os tokens usados (`response_metadata` / `usage_metadata`, quando existir) para a Etapa 11.

## Validação em código (depois do schema)
Índices existem, intervalos não se sobrepõem, respeitam limites (ex.: no máximo 30% do clipe removido; no máximo 1 imagem a cada 3 a 5 s; no máximo 1 zoom a cada ~8 s). Item inválido é descartado com aviso; não se "conserta" a resposta.

## Testes
Nunca chame a API nos testes unitários. Faça monkeypatch de uma fábrica interna (ex.: `client._make_model`) que devolve um objeto falso cujo `with_structured_output(...).invoke(...)` retorna a instância desejada ou lança exceção. Teste: sucesso, schema inválido → retry → `LLMError`, e o fallback de quem chama.
