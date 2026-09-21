# Testes pendentes

Aqui estão os testes que dependem de você: gravar vídeos, colocar arquivos no projeto ou olhar o resultado.
Depois de colocar os arquivos, avise: **"leia o testes-pendentes.md e execute os testes"**. Eu rodo tudo e atualizo o status aqui.

Os testes automáticos rodam dentro de `API/` (`cd API && uv run pytest -m integration`). Você também pode fazer quase tudo pelo **frontend** (seção 3, item C5).

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
| `PEXELS_API_KEY` | https://www.pexels.com/api/ (grátis) | ✅ definida |
| `PIXABAY_API_KEY` | https://pixabay.com/api/docs/ (grátis) | vazia |

---

## 2. Testes que eu executo quando os arquivos chegarem

| # | Etapa | Teste | Precisa de | Status |
|---|---|---|---|---|
| T1 | 2 | Teste de integração `tests/test_transcribe_integration.py`: Whisper real em `samples/1.mp4`, tempos das palavras em ordem, e a 2ª execução usando o cache em menos de 1 s (`cd API && uv run pytest -m integration`) | `samples/1.mp4` | ⏳ |
| T2 | 2 | Transcrever `samples/2.mp4` e conferir se as hesitações ("é...", "hm", "né") e as repetições aparecem na transcrição, sem terem sido "limpas" pelo Whisper | `samples/2.mp4` | ⏳ |
| T4 | 3 | `tests/test_pipeline_integration.py`: pipeline completo (transcrição, cortes e render) em `samples/1.mp4` + `samples/2.mp4`. Confere 30 fps, duração menor que a original e nenhuma pausa maior que ~0,66 s no resultado. Salva o vídeo em `output/etapa3_samples.mp4` para a checagem C2 | `samples/1.mp4`, `samples/2.mp4` | ⏳ |
| T5 | 4 | `tests/test_llm_integration.py`: LLM real (gpt-5-mini) em `samples/2.mp4`. Confere que ele marca pelo menos um erro de fala, que remove no máximo 30% do clipe, e mostra o que foi marcado | `samples/2.mp4` + `OPENAI_API_KEY` (já configurada) | ⏳ |
| T6 | 5 | `tests/test_face_integration.py`: rastreio de rosto em `samples/3.mp4`. Confere rosto em mais de 60% do vídeo, começo centralizado enquanto não há rosto, ausência de tremor e uso do cache na 2ª execução, e gera `output/etapa5_debug_samples3.mp4` | `samples/3.mp4` | ⏳ |
| T7 | 6 | `tests/test_face_integration.py::test_t7_...`: pipeline completo em `samples/3.mp4` com reenquadramento 9:16. Confere 1080x1920 a 30 fps e gera `output/etapa6_samples3_9x16.mp4` para a checagem C6 | `samples/3.mp4` | ⏳ |
| T3 | 1 | Ler `samples/4.mp4` com o ffprobe e confirmar que a resolução sai em pé (largura menor que a altura) | `samples/4.mp4` (opcional) | ⏳ |

## 3. Checagens que só você pode fazer

Eu preparo tudo e deixo aqui os tempos exatos para você conferir no player (VLC, por exemplo, com o tempo exibido em segundos).

| # | Etapa | O que conferir | Como | Status |
|---|---|---|---|---|
| C1 | 2 | Os timestamps das palavras batem com o áudio em 3 pontos (começo, meio e fim) | Depois do T1, eu escrevo aqui 3 palavras de `samples/1.mp4` com o tempo de cada uma. Você abre o vídeo, pula para cada tempo e confirma se a palavra é dita ali (tolerância de cerca de 0,3 s) | ⏳ |
| C2a | 3 | **Já dá para fazer agora:** ouça `output/etapa3_voz_sintetica.mp4` (resultado dos cortes em 2 clipes de voz sintética; os originais são `output/etapa3_original_1.mp4` e `_2.mp4`). Confira se nenhuma palavra foi cortada, sem estalos nas emendas em 1,5 / 5,6 / 7,1 / 7,9 / 10,1 / 12,8 s e com o fim de "simples." (~6,6 s) inteiro | Nada: os arquivos já estão em `output/` | 👀 |
| C2 | 3 | O vídeo cortado não tem pausas longas, **não corta o começo nem o fim das palavras** e **não tem estalos ("clicks") nas emendas** | Depois do T4, assista `output/etapa3_samples.mp4` com fone de ouvido. Se alguma palavra parecer cortada, me diga o segundo aproximado: dá para aumentar a margem (`--margem 0.12`) ou mudar o limiar (`--ruido-db -40`) | ⏳ |
| C3a | 4 | **Já dá para fazer agora:** compare `output/etapa4_original_erros.mp4` (voz sintética com falso começo, "o o o", take errado com "errei, vou de novo" e hesitações) com `output/etapa4_resultado.mp4`. Confira se os cortes soam naturais e se nada importante sumiu | Nada: os arquivos já estão em `output/` | 👀 |
| C3 | 4 | Com a sua voz: no frontend, importe `samples/` e rode "Gerar vídeo" com os cortes do LLM ligados (ou, em `API/`, `uv run python -m src.pipeline ../samples/2.mp4 -o ../output/etapa4_samples.mp4`) e confira se o LLM removeu os erros que você gravou de propósito e **nada além disso**. Atenção à repetição "o o o": o Whisper às vezes junta tudo num "o" só antes de o LLM ver. Também preste atenção às emendas no meio da fala, sem pausa: palavras de uma vogal só coladas à vizinha (ex.: o "é" em "problema é que") podem deixar um restinho de som | `samples/2.mp4` | ⏳ |
| C4a | 5 | **Já dá para fazer agora:** assista `output/etapa5_debug_rosto.mp4` (foto de teste parada com a câmera tremendo, depois andando para a direita). A caixa **verde** (suavizada) deve acompanhar o rosto **sem tremer**, mesmo quando a vermelha (detecção bruta) treme. Durante o movimento, a verde fica um pouco atrás da vermelha (zona morta de 2%); isso é esperado | Nada: o arquivo já está em `output/` | 👀 |
| C4 | 5 | O mesmo, com o seu vídeo: depois do T6, assista `output/etapa5_debug_samples3.mp4` (ou, no frontend, rode "Rastrear rosto" e abra o vídeo de debug do clipe). A **cruz azul-clara** (câmera) deve andar suave, a **caixa verde** deve cobrir o rosto e, nos 2 primeiros segundos sem rosto, a cruz fica no centro e não há caixa. Grave o `3.mp4` com o rosto ocupando pelo menos ~15% da altura do quadro: o detector não pega rostos menores que ~13% (plano muito aberto) | `samples/3.mp4` | ⏳ |
| C6a | 6 | **Já dá para fazer agora:** compare `output/etapa6_demo_original.mp4` (1920x1080, a foto de teste indo e voltando pelo quadro, com fala) com `output/etapa6_demo_9x16.mp4` (resultado vertical). O rosto deve ficar centralizado, a câmera deve andar suave, **sem tremor**, e o áudio deve bater com o vídeo no começo, no meio e no fim | Nada: os arquivos já estão em `output/` | 👀 |
| C6 | 6 | Com o seu vídeo: depois do T7 (ou no frontend, "Gerar vídeo" com "vertical 9:16" marcado), assista `output/etapa6_samples3_9x16.mp4`. O rosto deve estar sempre no quadro, a câmera deve andar suave quando você anda, e nos 2 s iniciais sem rosto o enquadramento fica no centro. Confira a sincronia da fala com a boca no começo, no meio e no fim | `samples/3.mp4` | ⏳ |
| C5 | 5b | **Frontend:** com a API (`cd API && uv run python -m src.api`) e o frontend (`cd frontend && npm install && npm run dev`) rodando, abra http://localhost:5173. Crie um projeto, importe a pasta `samples` (ou envie vídeos), reordene os clipes, rode "Gerar vídeo" e assista o resultado. Anote qualquer erro ou tela confusa: o design será feito na Etapa 10 | Nada além dos vídeos | 👀 |

---

## Histórico
- Etapa 2: validada com uma fala sintética gerada pela voz do Windows. O Whisper acertou as 28 palavras e os tempos batem com as pausas detectadas pelo `silencedetect` (diferença de até 0,2 s). A exceção é o início da primeira palavra depois de uma pausa longa, que o Whisper antecipa em cerca de 0,4 s; a Etapa 3 compensa isso alinhando os cortes ao `silencedetect`. Falta a validação com a sua voz real (T1, T2 e C1).
- Etapa 3: validada com 2 clipes de voz sintética com pausas de 1 a 2 s. O vídeo saiu com 57% de corte, nenhuma pausa de 0,4 s ou mais, as 32 palavras intactas (conferido transcrevendo o resultado), áudio e vídeo com a mesma duração e as emendas sem estalos (medido no áudio). O verificador reprovou 3 vezes antes de aprovar, e cada rodada corrigiu um problema real:
  - o fim das palavras era cortado;
  - uma pausa não detectada acabava mantida;
  - pausas com ruído de fundo continuavam no vídeo.
- Etapa 4: validada com o LLM real (gpt-5-mini) num clipe de voz sintética com erros de propósito. Ele marcou o falso começo ("Hoje eu vou,"), o take errado inteiro ("...100 graus, não, peraí, errei, vou de novo.") e a hesitação ("É, hm,"). Custo da chamada: cerca de 1,2 mil tokens de entrada e 1,6 mil de saída, em 16 s. Duas limitações observadas:
  - O Whisper transcreveu "o o o problema" como "Oh, o problema": juntou a repetição antes de o LLM vê-la, e sobrou um "oh".
  - **Limitação aceita:** cortar uma palavra solta no meio da fala contínua, sem pausa ao redor. As bordas desses cortes vão para o silêncio real entre as palavras e, na dúvida, preservam a palavra mantida. Numa varredura removendo 34 palavras da voz sintética, uma de cada vez: 11 limpas, 15 com resto da palavra removida e 6 com a vizinha possivelmente afetada (a maioria parece erro da retranscrição usada para medir). Os cortes reais do LLM, que caem em fronteiras de frase, saíram limpos. Se a sua voz real mostrar problema (C3), a alternativa é um alinhador por fonemas (wav2vec2, ~1 GB, PyTorch).
  - O Whisper "ouviu" um "tchau, tchau" no silêncio do fim. Isso é alucinação dele, e o corte de silêncio remove esse trecho de qualquer forma.
- Etapa 5: validada com vídeos sintéticos (foto de teste do MediaPipe com trajetória conhecida). Achado importante: o detector de rosto do MediaPipe (BlazeFace de curto alcance) reduz a imagem para 128x128 e **não detectava** rostos de tamanho normal em vídeo horizontal. Por isso a detecção roda em 3 recortes quadrados sobrepostos do quadro. Resultado: erro de no máximo 3% da largura com o rosto em movimento (o atraso da zona morta), sem tremor, rosto parado estável com a câmera tremendo e escolha certa entre dois rostos.
- Etapa 6: janela 9:16 seguindo o caminho da câmera da Etapa 5, com velocidade máxima (meia largura de quadro por segundo) aplicada sem atraso. O render é feito em duas passadas: na 1ª, o FFmpeg decodifica, o OpenCV recorta para 1080x1920 e outro FFmpeg codifica junto com o áudio; na 2ª, os trechos são concatenados. A sincronia foi medida com flashes e bipes sintéticos no começo, no meio e no fim, e ficou em menos de 5 ms. Essa medição **encontrou um defeito que vinha desde a Etapa 3**: o seek de áudio deixava o AAC 13 a 40 ms adiantado. Foi corrigido com `atrim`.
