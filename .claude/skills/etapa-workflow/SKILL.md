---
name: etapa-workflow
description: Fluxo para implementar uma etapa do etapas.md do editor de vídeos. Use quando o usuário pedir "implemente a Etapa N", "próxima etapa" ou perguntar o que falta no projeto.
---

# Implementar uma etapa do etapas.md

1. Leia `etapas.md` inteiro. Confirme que as etapas anteriores estão marcadas `[x]`; se não estiverem, avise antes de seguir (não pule etapas).
2. Releia a tabela **Decisões fixas** e as **Regras de uso do LangChain**. Não reabra decisões sem motivo; se uma decisão não funcionar na prática, explique e pergunte.
3. Consulte a doc atual da biblioteca pelo Context7 antes de usar APIs de faster-whisper, MediaPipe, LangChain, PySide6 ou rembg. Veja também as skills `ffmpeg-video`, `langchain-structured` e `mediapipe-face`.
4. Implemente só o escopo da etapa, nos arquivos da estrutura alvo. Os módulos futuros já existem como stubs com docstring.
5. Escreva testes em `tests/` para cada critério de aceite automatizável. Testes que precisam de vídeo real, GPU ou rede levam `@pytest.mark.integration`. Gere vídeos sintéticos com FFmpeg (`testsrc2`, `sine`, `anullsrc`) em `tmp_path` sempre que der.
6. Rode `uv run pytest -q` e `uv run python -m src.doctor`.
7. Delegue a checagem final ao subagente `etapa-verifier` ("verifique a Etapa N"). Só marque `- [x] Etapa N` no checklist do etapas.md se ele aprovar todos os critérios.
8. Registre em `testes-pendentes.md` todo teste que depende do usuário: arquivos em `samples/` (nome exato e o que o vídeo precisa conter), chaves de API ou checagem visual/auditiva. Não duplique: reaproveite os vídeos já pedidos sempre que der. Quando o usuário avisar que colocou os arquivos, execute os testes listados e atualize o status.
9. Liste para o usuário os critérios que exigem checagem manual (ex.: "timestamps conferem com o áudio") e sugira o commit (`git add -A && git commit -m "Etapa N: ..."`). Não faça commit sem o usuário pedir.

## Convenções
- Ambiente: `uv` + Python 3.12 (`.python-version`). Adicione dependências com `uv add`, nunca editando o `uv.lock`.
- Configuração só via `src.config.get_settings()`; não leia `os.environ` nos módulos.
- Logging: `log = logging.getLogger(__name__)`; entrypoints chamam `src.logging_setup.setup_logging()`.
- Tempo sempre em segundos (`float`). Timestamps originais de cada clipe e timestamps finais são conceitos distintos; nomeie as variáveis de forma explícita (`t_src`, `t_out`).
- Mensagens e docstrings em português.
