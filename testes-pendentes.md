# Testes pendentes

Aqui estão os testes que dependem de você: gravar vídeos, colocar arquivos no projeto ou olhar o resultado.
Depois de colocar os arquivos, avise: **"leia o testes-pendentes.md e execute os testes"**. Eu rodo tudo e atualizo o status aqui. Testes concluídos saem da lista; o resultado de cada um fica no **Histórico**, no fim do arquivo.

Os testes automáticos rodam dentro de `API/` (`cd API && uv run pytest -m integration`). Você também pode fazer quase tudo pelo **frontend** (seção 3, item C5).

Status: ⏳ aguardando arquivos · 👀 aguardando sua checagem visual/auditiva

---

## 1. Arquivos que você precisa colocar

Todos na pasta **`samples/`**, na raiz do projeto:
`C:\Users\massa\Desktop\AGENTES\Editor de Videos\samples\`

A pasta `samples/` não vai para o GitHub (está no `.gitignore`), então pode usar vídeos pessoais.

### Requisitos comuns a todos os vídeos
- Formato `.mp4`, a 30 fps. Pode ser na horizontal (16:9) ou em pé (celular).
- **Você falando em português, perto da câmera e olhando para ela**, com o rosto ocupando pelo menos ~15% da altura do quadro. O detector não pega rostos menores, como os de um plano aberto de palestra.
- Áudio limpo, sem música de fundo.
- Duração entre **20 e 45 segundos** cada.

| Arquivo (nome exato) | Usado nas etapas | O que precisa ter |
|---|---|---|
| `samples/2.mp4` | 4, 11 | Fala com **erros de propósito**: um falso começo ("Hoje eu vou... hoje a gente vai falar"), uma repetição ("o o o problema é") e um take errado seguido de *"errei, vou de novo"*, repetindo a frase certa em seguida. Inclua hesitações como "é...", "hm" e "né". |
| `samples/3.mp4` | 5, 6, 9, 11 | Comece com **2 segundos sem rosto** (câmera apontada para outro lugar ou você fora do quadro). Depois entre no quadro e fale **andando devagar de um lado para o outro** (esquerda → centro → direita), perto da câmera. Em algum momento **enfatize uma frase** com mais energia, para testar o zoom. |
| `samples/1.mp4` | 7, 8, 11 | Fala normal com pausas, citando pelo menos 3 objetos concretos, por exemplo "frutas", "carro", "cachorro", "computador". Vai servir para as legendas e as imagens. Com o 2 e o 3, completa os 3 clipes do fluxo final. |

### Chaves de API (no seu `.env`, só a partir da Etapa 8)
| Variável | Onde conseguir | Situação atual |
|---|---|---|
| `PEXELS_API_KEY` | https://www.pexels.com/api/ (grátis) | ✅ definida |
| `PIXABAY_API_KEY` | https://pixabay.com/api/docs/ (grátis) | vazia (opcional: é só o fallback) |

---

## 2. Testes que eu executo quando os arquivos chegarem

| # | Etapa | Teste | Precisa de | Status |
|---|---|---|---|---|
| T5 | 4 | `tests/test_llm_integration.py`: LLM real em `samples/2.mp4`. Confere que ele marca os erros gravados de propósito e remove no máximo 30% do clipe | `samples/2.mp4` | ⏳ |
| T6 | 5 | `tests/test_face_integration.py`: rastreio de rosto em `samples/3.mp4`. Confere rosto em mais de 60% do vídeo, começo centralizado sem rosto, ausência de tremor e cache, e gera `output/etapa5_debug_samples3.mp4` | `samples/3.mp4` | ⏳ |
| T8 | 11 | `tests/test_fluxo_integration.py`: fluxo completo pela API (criar projeto → importar → transcrever → rosto → gerar) e a prévia aprovada sendo reaproveitada no render sem chamar o LLM de novo. Confere 1080x1920, áudio e vídeo com a mesma duração e o uso do LLM (tokens e custo) | 2 ou mais vídeos em `samples/` | ⏳ |

## 3. Checagens que só você pode fazer

Abra os vídeos num player que mostre o tempo em segundos (o VLC, por exemplo).

### Com o seu vídeo da palestra (já prontas)

| # | Etapa | O que conferir | Como | Status |
|---|---|---|---|---|
| C1 | 2 | Os tempos das palavras batem com o áudio | Abra `samples/WhatsApp Video 2026-09-21 at 16.08.56.mp4` e pule para cada tempo; a palavra deve ser dita ali, com tolerância de ~0,3 s: **2,72 s** "mudar..." ("...e não quiser *mudar*... Porque aqui, gente"), **35,12 s** "mexidos" ("...com ovos *mexidos*, com quiche") e **64,02 s** "pessoal" ("...tem um preço, *pessoal*. Por um tempo") | 👀 |
| C2 | 3 | O vídeo cortado não tem pausas longas, **não corta o começo nem o fim das palavras** e **não tem estalos nas emendas** | Assista `output/samples_palestra_9x16.mp4` com fone. São 19 trechos: 69,2 s viraram 55,7 s. Se alguma palavra parecer cortada, me diga o segundo aproximado | 👀 |
| C3b | 4 | O LLM cortou 4 "ah" como hesitação, mas neles a palestrante está imitando alguém falando: "você fala, **ah**, não dá". Esses cortes deixam a fala estranha? | No original, os "ah" estão em **12,86 s**, **22,90 s**, **26,58 s** e **28,90 s**. Ouça essas frases no `output/samples_palestra_9x16.mp4`. Se estiverem estranhas, eu deixo o prompt mais conservador ("ah" com sentido, como em fala imitada, não é cortado) | 👀 |
| C7 | 7 | As legendas aparecem **no momento em que cada palavra é dita**, a palavra destacada em amarelo acompanha a fala, o texto fica **legível** na parede clara e sobre as roupas escuras, e está acima da área de botões das redes | Assista `output/etapa7_palestra_legendas.mp4` (a palestra com cortes, vertical e legendas). Repare também se a linha "dança" na horizontal quando a palavra destacada cresce (112%); se incomodar, dá para desligar o aumento. Se o tamanho, a cor ou a posição não agradarem, dá para mudar no frontend (tamanho, cor, destaque e maiúsculas) | 👀 |
| C8 | 8 | As imagens aparecem **na hora da palavra** (exames, café, ovos, cachorro, fila), **não cobrem o rosto da palestrante nem as legendas** e combinam com o que ela fala | Assista `output/etapa8_palestra_imagens.mp4`. Se alguma foto for ruim, troque na prévia (C8b) | 👀 |
| C8b | 8 | Prévia de imagens no frontend: "Sugerir imagens (preview)" mostra as fotos escolhidas, ◀ ▶ troca a foto, "usar" liga/desliga, "buscar de novo" muda a busca e "Gerar vídeo" usa as suas escolhas **sem chamar o LLM de novo** (o log do job diz "Usando o plano criativo salvo") | Com a API e o frontend rodando, abra o projeto com `samples/` e teste o painel "Imagens (preview)" | 👀 |
| C9 | 9 | Os zooms no rosto são **suaves** (sem salto nem tremida), entram em momentos de ênfase e **não cortam o rosto**; as imagens continuam fora do rosto e das legendas durante o zoom | Assista `output/etapa9_zoom.mp4`. Os zooms estão em **13,2–14,7 s** ("passou"), **24,4–25,9 s** ("quer") e **54,7–55,7 s** ("chega..."). Neste vídeo o rosto rastreado é de uma pessoa da plateia (ver T6), então o zoom se aproxima do centro do enquadramento, não da palestrante. O teste com você perto da câmera fica para o `3.mp4` (C6) | 👀 |
| C9b | 9 | Frontend: a lista de zooms aparece na prévia ("Sugerir imagens e zooms"), cada zoom liga/desliga e a opção "Zooms no rosto" desliga todos | Com a API e o frontend rodando, abra o projeto com `samples/` | 👀 |
| C10 | 10 | A interface nova (tema escuro, palco + faixa de clipes + opções) faz sentido para você: criar/abrir projeto, importar, **arrastar clipes para reordenar**, gerar, acompanhar o progresso e assistir o resultado sem travar | Com a API (`cd API && uv run python -m src.api`) e o frontend (`cd frontend && npm run dev`) rodando, abra http://localhost:5173. Eu já conferi no navegador: layout em 1440 e 900 px, arrastar, importar pasta, remover clipe, opções travadas durante o job e o vídeo final refletindo a nova ordem. Falta seu julgamento de uso e aparência | 👀 |
| C10b | 10 | A escolha do **modelo do LLM** nas opções funciona no seu uso (o padrão é o do `.env`) | Nas Opções, em Cortes, escolha `openai:gpt-5` e gere; o log do job deve mostrar o modelo usado | 👀 |
| C11 | 11 | **Rodar tudo com um comando só**: `cd frontend && npm run build` e depois `cd API && uv run python -m src.api`; abra http://127.0.0.1:8000 e use normalmente (sem o `npm run dev`) | Eu conferi que a página e a API respondem; falta você usar assim no dia a dia | 👀 |
| C11b | 11 | **Avisos e custo**: gere um vídeo e veja se os avisos dos clipes (sem áudio, fps variável, HDR, pouco rosto) e a linha do LLM (chamadas, tokens e custo estimado) fazem sentido para você | Painel do job, depois de concluído. Os preços da tabela em `API/src/llm/pricing.py` podem estar desatualizados: confira uma fatura real se for se guiar por eles | 👀 |
| C11c | 11 | **Mensagem de erro clara**: renomeie temporariamente sua chave no `.env` (ex.: `OPENAI_API_KEY_X=`) e gere com o LLM ligado; a mensagem deve explicar o que fazer | Depois é só desfazer o rename | 👀 |
| C6b | 6 | O vídeo vertical 1080x1920 está bom e a fala bate com a boca no começo, no meio e no fim | Assista `output/samples_palestra_9x16.mp4`. O vídeo original já era em pé, então o enquadramento quase não se move; confira a nitidez (houve ampliação de 480 para 1080 de largura) e a sincronia | 👀 |

### Com vídeos que ainda faltam

| # | Etapa | O que conferir | Como | Status |
|---|---|---|---|---|
| C3 | 4 | O LLM remove os erros gravados de propósito e **nada além disso** | Depois do T5: no frontend, importe `samples/` e rode "Gerar vídeo" com os cortes do LLM ligados. Atenção ao "o o o": o Whisper às vezes junta tudo num "o" só antes de o LLM ver | ⏳ `2.mp4` |
| C4 | 5 | A caixa do rosto acompanha você sem tremer | Depois do T6, assista `output/etapa5_debug_samples3.mp4`. A **cruz azul-clara** (câmera) deve andar suave, a **caixa verde** deve cobrir o rosto e, nos 2 s iniciais sem rosto, a cruz fica no centro | ⏳ `3.mp4` |
| C6 | 6, 9 | Vertical 9:16 seguindo você quando anda, com zooms no **seu** rosto | No frontend, "Gerar vídeo" com "vertical 9:16" e `samples/3.mp4`. O rosto deve ficar sempre no quadro, a câmera deve andar suave, os zooms devem aproximar do seu rosto sem cortá-lo e a fala deve bater com a boca | ⏳ `3.mp4` |

### Com vídeos sintéticos (já prontas, em `output/`)

| # | Etapa | O que conferir | Status |
|---|---|---|---|
| C2a | 3 | Ouça `output/etapa3_voz_sintetica.mp4` (os originais são `etapa3_original_1.mp4` e `_2.mp4`): nenhuma palavra cortada e sem estalos nas emendas em 1,5 / 5,6 / 7,1 / 7,9 / 10,1 / 12,8 s | 👀 |
| C3a | 4 | Compare `output/etapa4_original_erros.mp4` com `output/etapa4_resultado.mp4`: os cortes de erros de fala soam naturais e nada importante sumiu | 👀 |
| C4a | 5 | Assista `output/etapa5_debug_rosto.mp4`: a caixa verde acompanha o rosto sem tremer | 👀 |
| C6a | 6 | Compare `output/etapa6_demo_original.mp4` com `output/etapa6_demo_9x16.mp4`: o rosto fica centralizado, a câmera anda suave e o áudio bate com o vídeo | 👀 |

### Interface

| # | Etapa | O que conferir | Status |
|---|---|---|---|
| C5 | 5b | Com a API (`cd API && uv run python -m src.api`) e o frontend (`cd frontend && npm install && npm run dev`) rodando, abra http://localhost:5173. Crie um projeto, importe a pasta `samples`, rode "Gerar vídeo" e assista o resultado. Anote qualquer erro ou tela confusa: o design é na Etapa 10 | 👀 |

---

## Histórico

### Testes concluídos com o seu vídeo (21/09/2026)
Vídeo: `samples/WhatsApp Video 2026-09-21 at 16.08.56.mp4`. É uma palestra filmada da plateia com o celular em pé: 69 s, 848x480 com rotação de −90°.

- ✅ **T1: transcrição real.** 191 palavras em 22,6 s na GPU, com tempos em ordem. A 2ª execução usou o cache e levou 0,01 s. O texto saiu muito fiel.
- ✅ **T2: hesitações preservadas.** O Whisper manteve "tipo", "ah", "né" e "é" e a repetição "e não deu, e não deu", sem "limpar" a fala.
- ✅ **T3: vídeo de celular em pé.** Lido como 480x848 (largura menor que a altura), com rotação de 270°. Substituiu o `4.mp4`.
- ✅ **T4: cortes de silêncio.** Pausas de até 3,5 s no original. No resultado, a maior pausa é de 0,38 s (limite 0,66 s). Áudio e vídeo com exatamente 55,733 s.
- ✅ **T5 (parcial, sem erros de propósito): LLM real.** 25 s e cerca de 2,4 mil tokens de entrada e 2,2 mil de saída. Marcou 4 "ah" como hesitação e mais nada. O teste com erros gravados de propósito continua no T5, e o julgamento dos "ah" está no C3b.
- ✅ **T7: vertical 9:16.** Saída de 1080x1920 a 30 fps.
- ⚠️ **T6 com este vídeo (não conta como aprovado): rosto.** Detectado em 39% dos frames, com tremor no p99 de 0,0045 (limite 0,003). O detector **se prendeu a uma pessoa da plateia** (cabelo ruivo, no centro) e não à palestrante, porque a regra atual escolhe o rosto maior e mais central, e a palestrante está longe (rosto com ~10% da altura). O debug está em `output/samples_palestra_debug_rosto.mp4`. Neste vídeo isso não afetou o resultado: o original já é em pé, então a janela 9:16 praticamente não se move. Para zoom e imagens (Etapas 8 e 9), mirar o rosto errado seria um problema. Filmagens de palestra precisariam de detecção de "quem está falando", que fica fora do escopo por enquanto. O T6 com o `3.mp4` (você perto da câmera) continua pendente.

### Validações anteriores (vídeos sintéticos)
- Etapa 2: validada com uma fala sintética gerada pela voz do Windows. O Whisper acertou as 28 palavras e os tempos batem com as pausas detectadas pelo `silencedetect` (diferença de até 0,2 s). A exceção é o início da primeira palavra depois de uma pausa longa, que o Whisper antecipa em cerca de 0,4 s; a Etapa 3 compensa isso alinhando os cortes ao `silencedetect`.
- Etapa 3: validada com 2 clipes de voz sintética com pausas de 1 a 2 s. O vídeo saiu com 57% de corte, nenhuma pausa de 0,4 s ou mais, as 32 palavras intactas (conferido transcrevendo o resultado), áudio e vídeo com a mesma duração e as emendas sem estalos (medido no áudio). O verificador reprovou 3 vezes antes de aprovar, e cada rodada corrigiu um problema real:
  - o fim das palavras era cortado;
  - uma pausa não detectada acabava mantida;
  - pausas com ruído de fundo continuavam no vídeo.
- Etapa 4: validada com o LLM real (gpt-5-mini) num clipe de voz sintética com erros de propósito. Ele marcou o falso começo ("Hoje eu vou,"), o take errado inteiro ("...100 graus, não, peraí, errei, vou de novo.") e a hesitação ("É, hm,"). Custo da chamada: cerca de 1,2 mil tokens de entrada e 1,6 mil de saída, em 16 s. Limitações observadas:
  - O Whisper transcreveu "o o o problema" como "Oh, o problema": juntou a repetição antes de o LLM vê-la, e sobrou um "oh".
  - **Limitação aceita:** cortar uma palavra solta no meio da fala contínua, sem pausa ao redor. As bordas desses cortes vão para o silêncio real entre as palavras e, na dúvida, preservam a palavra mantida. Numa varredura removendo 34 palavras da voz sintética, uma de cada vez: 11 limpas, 15 com resto da palavra removida e 6 com a vizinha possivelmente afetada (a maioria parece erro da retranscrição usada para medir). Os cortes reais do LLM, que caem em fronteiras de frase, saíram limpos. Se a sua voz real mostrar problema (C3), a alternativa é um alinhador por fonemas (wav2vec2, ~1 GB, PyTorch).
  - O Whisper "ouviu" um "tchau, tchau" no silêncio do fim. Isso é alucinação dele, e o corte de silêncio remove esse trecho de qualquer forma.
- Etapa 5: validada com vídeos sintéticos (foto de teste do MediaPipe com trajetória conhecida). Achado importante: o detector de rosto do MediaPipe (BlazeFace de curto alcance) reduz a imagem para 128x128 e **não detectava** rostos de tamanho normal em vídeo horizontal. Por isso a detecção roda em 3 recortes quadrados sobrepostos do quadro. Resultado: erro de no máximo 3% da largura com o rosto em movimento (o atraso da zona morta), sem tremor, rosto parado estável com a câmera tremendo e escolha certa entre dois rostos.
- Etapa 6: janela 9:16 seguindo o caminho da câmera da Etapa 5, com velocidade máxima (meia largura de quadro por segundo) aplicada sem atraso. O render é feito em duas passadas: na 1ª, o FFmpeg decodifica, o OpenCV recorta para 1080x1920 e outro FFmpeg codifica junto com o áudio; na 2ª, os trechos são concatenados. A sincronia foi medida com flashes e bipes sintéticos no começo, no meio e no fim, e ficou em menos de 5 ms. Essa medição **encontrou um defeito que vinha desde a Etapa 3**: o seek de áudio deixava o AAC 13 a 40 ms adiantado. Foi corrigido com `atrim`.
- Etapa 7: legendas palavra por palavra em Poppins Bold (licença OFL), com o destaque em amarelo, queimadas na passada 2. O verificador reprovou 3 vezes antes de aprovar, e cada rodada corrigiu um problema real:
  - o arredondamento dos tempos fazia uma legenda vazar 1 frame para o clipe seguinte;
  - o libass desenhava a fonte com 57% do tamanho pedido, porque escala pelas métricas win da fonte;
  - o filtro de restos de palavras cortadas apagava palavras reais ("A gente", "você", "quê").
  Resultado na palestra: 186 legendas, cada uma a no máximo 6,7 ms da palavra, nenhuma atravessando emenda, e legíveis em parede clara, roupa escura e colete neon.
- Etapa 8: o LLM escolheu 5 palavras concretas na palestra (exames, café, ovos, cachorro, fila), com 1 chamada (~2,2 mil tokens de entrada) e resultado em cache. As fotos vêm do Pexels (horizontais) e ficam no topo, desviando da caixa real do rosto e da legenda. A prévia deixa trocar as fotos sem chamar o LLM de novo.
