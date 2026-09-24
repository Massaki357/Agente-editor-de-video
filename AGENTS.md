# Instruções para agentes — Editor de Vídeos

## Contexto

Este projeto gera vídeos verticais 1080x1920 a partir de clipes. O núcleo e a API FastAPI ficam em `API/`; a interface React/TypeScript fica em `frontend/`. Leia `CLAUDE.md` para o mapa do código e as regras já adotadas. `etapas.md` registra o plano principal; `novas-etapas.md` registra as ferramentas adicionais e seus checklists. `testes-pendentes.md` guarda verificações que dependem de vídeos do usuário ou avaliação visual/auditiva.

## Trabalho por etapa

- Implemente uma etapa de cada vez, respeitando as decisões fixas e o escopo descrito nos planos. Verifique tarefas e critérios antes de marcar um checklist como concluído. Registre testes manuais pendentes em `testes-pendentes.md`.
- Funcionalidade nova entra primeiro em `API/src/`, depois na API como job quando houver processamento pesado, com controle mínimo no frontend quando a etapa pedir integração. Um projeto com job ativo não pode ser alterado.
- Preserve a divisão de responsabilidades: o LLM escolhe conteúdo e tempo; o código decide geometria. Imports de LangChain ficam em `API/src/llm/client.py`; prompts ficam em `API/src/llm/prompts/`.
- Configuração passa por `src.config.get_settings()`. Não edite `API/uv.lock` manualmente. Não altere `.env`, vídeos em `samples/` nem arquivos de cache do usuário.
- Áudio limpo afeta somente o áudio final; a transcrição usa o original. Estabilização de vídeo, quando integrada, deve ocorrer por clipe antes do rastreio de rosto e do reenquadramento.

## Comandos e verificação

- Execute `uv` dentro de `API/`: `uv run pytest -q`, `uv run python -m src.doctor` e `uv run python -m src.api --reload`.
- Execute o frontend dentro de `frontend/`: `npm run dev` e `npm run build`.
- Testes que dependem de vídeos reais, GPU, rede ou LLM pago usam `@pytest.mark.integration`. Prefira casos sintéticos para testes rápidos. Não marque como aprovado um critério que ainda exige conferência visual ou auditiva.
- Nas ferramentas novas, consulte o checklist de `novas-etapas.md` e avance da primeira etapa pendente após a verificação da anterior.
