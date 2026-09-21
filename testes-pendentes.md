# Testes pendentes

Aqui estão os testes que dependem de você: gravar vídeos, colocar arquivos no projeto ou olhar o resultado.
Depois de colocar os arquivos, avise: **"leia o testes-pendentes.md e execute os testes"**. Eu rodo tudo e atualizo o status aqui.

Status: ⏳ aguardando arquivos · 👀 aguardando sua checagem visual/auditiva · ✅ concluído

---

## 1. Arquivos que você precisa colocar

Todos na pasta **`samples/`**, na raiz do projeto:
`C:\Users\massa\Desktop\AGENTES\Editor de Videos\samples\`

A pasta `samples/` não vai para o GitHub (está no `.gitignore`), então pode usar vídeos pessoais.

### Requisitos comuns a todos os vídeos
- Formato `.mp4`, gravado na **horizontal** (16:9, de preferência 1920x1080), a 30 fps.
- **Você falando em português**, olhando para a câmera, com o rosto visível na maior parte do tempo.
- Áudio limpo, sem música de fundo.
- Duração entre **20 e 45 segundos** cada. Vídeos curtos deixam os testes rápidos.

| Arquivo (nome exato) | Usado nas etapas | O que precisa ter |
|---|---|---|
| `samples/1.mp4` | 2, 3, 5, 6, 7, 11 | Fala normal com **pelo menos 3 pausas longas** (fique 1 a 2 segundos calado entre frases). Cite pelo menos 3 objetos concretos, por exemplo "frutas", "carro", "cachorro", "computador". |
| `samples/2.mp4` | 4, 11 | Fala com **erros de propósito**: um falso começo ("Hoje eu vou... hoje a gente vai falar"), uma repetição ("o o o problema é") e um take errado seguido de *"errei, vou de novo"*, repetindo a frase certa em seguida. Inclua hesitações como "é...", "hm" e "né". |
| `samples/3.mp4` | 5, 6, 9, 11 | Comece com **2 segundos sem rosto** (câmera apontada para outro lugar ou você fora do quadro). Depois entre no quadro e fale **andando devagar de um lado para o outro** (esquerda → centro → direita). Em algum momento **enfatize uma frase** com mais energia, para testar o zoom. |
| `samples/4.mp4` *(opcional)* | 1, 6 | Um vídeo curto (5 a 10 s) **gravado com o celular na vertical**, para testar a rotação do celular. Pode ser qualquer fala. |

### Chaves de API (no seu `.env`, só a partir da Etapa 8)
| Variável | Onde conseguir | Situação atual |
|---|---|---|
| `PEXELS_API_KEY` | https://www.pexels.com/api/ (grátis) | vazia |
| `PIXABAY_API_KEY` | https://pixabay.com/api/docs/ (grátis) | vazia |

---

## 2. Testes que eu executo quando os arquivos chegarem

| # | Etapa | Teste | Precisa de | Status |
|---|---|---|---|---|
| T1 | 2 | Teste de integração `tests/test_transcribe_integration.py`: Whisper real em `samples/1.mp4`, tempos das palavras em ordem, e a 2ª execução usando o cache em menos de 1 s (`uv run pytest -m integration`) | `samples/1.mp4` | ⏳ |
| T2 | 2 | Transcrever `samples/2.mp4` e conferir se as hesitações ("é...", "hm", "né") e as repetições aparecem na transcrição, sem terem sido "limpas" pelo Whisper | `samples/2.mp4` | ⏳ |
| T3 | 1 | Ler `samples/4.mp4` com o ffprobe e confirmar que a resolução sai em pé (largura menor que a altura) | `samples/4.mp4` (opcional) | ⏳ |

## 3. Checagens que só você pode fazer

Eu preparo tudo e deixo aqui os tempos exatos para você conferir no player (VLC, por exemplo, com o tempo exibido em segundos).

| # | Etapa | O que conferir | Como | Status |
|---|---|---|---|---|
| C1 | 2 | Os timestamps das palavras batem com o áudio em 3 pontos (começo, meio e fim) | Depois do T1, eu escrevo aqui 3 palavras de `samples/1.mp4` com o tempo de cada uma. Você abre o vídeo, pula para cada tempo e confirma se a palavra é dita ali (tolerância de cerca de 0,3 s) | ⏳ |

---

## Histórico
- Etapa 2: validada com uma fala sintética gerada pela voz do Windows. O Whisper acertou as 28 palavras e os tempos batem com as pausas detectadas pelo `silencedetect` (diferença de até 0,2 s). A exceção é o início da primeira palavra depois de uma pausa longa, que o Whisper antecipa em cerca de 0,4 s; a Etapa 3 compensa isso alinhando os cortes ao `silencedetect`. Falta a validação com a sua voz real (T1, T2 e C1).
