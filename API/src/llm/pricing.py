"""Custo estimado das chamadas do LLM (Etapa 11).

Os preços são por **milhão de tokens** (entrada, saída) e mudam com o tempo: esta
tabela é só uma estimativa para o log e para a interface. Modelo fora da tabela →
custo desconhecido (`None`), e a interface mostra só os tokens.

Confira os preços atuais em https://platform.openai.com/docs/pricing e
https://docs.anthropic.com/en/docs/about-claude/pricing antes de confiar no valor.
"""

from __future__ import annotations

from typing import TypedDict

# modelo (como em LLM_MODEL) → (US$ por 1M de tokens de entrada, de saída)
PRECOS: dict[str, tuple[float, float]] = {
    "openai:gpt-5-mini": (0.25, 2.00),
    "openai:gpt-5": (1.25, 10.00),
    "anthropic:claude-sonnet-5": (3.00, 15.00),
    "anthropic:claude-haiku-4-5": (1.00, 5.00),
}


class UsoLLM(TypedDict):
    """Resumo de uma execução: quantas chamadas, quantos tokens e quanto custou."""

    chamadas: int
    tokens_entrada: int
    tokens_saida: int
    custo_usd: float | None  # None quando algum modelo usado não está na tabela
    modelos: list[str]


def resumir(totais: dict[str, dict[str, int]]) -> UsoLLM | None:
    """`client.usage_totals()` → resumo com custo estimado; None se não houve chamada."""
    if not totais:
        return None
    entrada = sum(v.get("input_tokens", 0) for v in totais.values())
    saida = sum(v.get("output_tokens", 0) for v in totais.values())
    chamadas = sum(v.get("calls", 0) for v in totais.values())
    custo: float | None = 0.0
    for modelo, v in totais.items():
        preco = PRECOS.get(modelo)
        if preco is None or custo is None:
            custo = None
            continue
        custo += v.get("input_tokens", 0) * preco[0] / 1e6
        custo += v.get("output_tokens", 0) * preco[1] / 1e6
    return UsoLLM(
        chamadas=chamadas,
        tokens_entrada=entrada,
        tokens_saida=saida,
        custo_usd=round(custo, 6) if custo is not None else None,
        modelos=sorted(totais),
    )


def descrever(uso: UsoLLM) -> str:
    """Linha curta para o log: `2 chamada(s), 5,4 mil tokens, ≈US$ 0,0041`."""
    tokens = uso["tokens_entrada"] + uso["tokens_saida"]
    custo = f", ≈US$ {uso['custo_usd']:.4f}" if uso["custo_usd"] is not None else ""
    return f"{uso['chamadas']} chamada(s), {tokens / 1000:.1f} mil tokens{custo}"
