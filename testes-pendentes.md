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
| P1-C3 | Parte 1, Etapa 3 | **Áudio limpo no vídeo gerado**: na interface, marque "limpar o ruído do áudio do vídeo" (grupo Áudio, no topo das opções) e gere um vídeo. Confira se o som do resultado ficou melhor que o do original e se continua **em sincronia com a boca** | Eu medi num render de teste: SNR 8,9 → 28,0 dB, volume −29,2 → −15,8 LUFS, áudio e vídeo com a mesma duração e deslocamento abaixo de 1 ms. Falta o seu ouvido no vídeo inteiro | 👀 |
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

### Catálogo de transições adicionais (25/09/2026)

- ✅ **P6-E0: catálogo validado tecnicamente.** Sete presets com limites e custo relativo; sete prévias sintéticas de 135 quadros mostram entrada e saída. A validação rejeita parâmetros inválidos, cutaways curtos e emendas antes do render. Os 22 testes focados, Ruff, doctor, build e revisão independente passaram; a suíte completa teve 603 testes aprovados e 19 integrações não executadas.
- 👀 **P6-C0: conferir os sete presets quando puder.** Abra `output/parte6_etapa0_catalogo/index.html` e assista às prévias sintéticas de corte seco, fusão, deslizamento, varredura, revelação, zoom e desfoque. Cada vídeo mostra câmera → B-roll → câmera. Confira se a entrada e a saída são perceptíveis e quais efeitos combinam com o estilo desejado; nenhum arquivo novo em `samples/` é necessário. Os novos presets ainda não estão ligados ao render final (Etapa 1).

### Interface do chat pós-render (25/09/2026)

- ✅ **P5-E7: sequência e robustez validadas tecnicamente.** Um teste com vídeo sintético aplicou cinco edições (três manuais e duas por chat), preservou todos os quadros do B-roll vizinho e da lacuna, confirmou IDs e intervalos, reutilização do cache, versões do histórico e restauração exata por Desfazer/Refazer. O README documenta os comandos do chat v1 e o replanejamento. Suíte completa: 582 testes aprovados, 19 integrações não executadas; teste focado, Ruff, doctor, build e revisão independente passaram.
- 👀 **P5-C7: conferir coerência após edições sucessivas num vídeo real.** Reaproveite o projeto de P5-C6: faça pelo menos cinco ajustes alternando controles manuais e chat, confirme cada prévia e assista ao vídeo final inteiro. Verifique se imagem, B-roll, zoom, legendas e áudio continuam no tempo certo, sem elementos órfãos ou sobrepostos; use **Desfazer/Refazer** no último ajuste. Essa avaliação perceptiva complementa o teste sintético da Etapa 7; nenhum arquivo novo em `samples/` é necessário.
- ✅ **P5-E6: fluxo técnico validado.** Testes sintéticos cobriram troca de imagem e remoção de zoom por chat, prévia automática, confirmação, IDs no histórico, pedidos ambíguos e replanejamento. A suíte completa teve 581 testes aprovados e 19 integrações não executadas; 11 testes focados passaram após o último ajuste. Ruff, doctor, build da interface e revisão independente aprovaram.
- 👀 **P5-C6: conferir o chat com um vídeo real já gerado.** Na aba **Edição**, peça “tira o zoom da parte 2” e “troca a imagem da cesta de frutas por outra”. Confira se o histórico mostra os IDs certos e se a prévia corresponde ao trecho antes de clicar em **Aplicar ao vídeo final**; depois confirme que o resultado mudou apenas no trecho esperado. Peça “melhora essa parte” e verifique que o agente pergunta qual elemento ajustar. Peça “refaz todas as imagens” e confira a orientação para **Sugerir imagens e zooms** e **Gerar vídeo**. Reaproveite o projeto de P5-C5; a pertinência da imagem e o resultado visual dependem de avaliação humana. Nenhum arquivo novo em `samples/` é necessário.

### Ferramentas do chat pós-render (25/09/2026)

- ✅ **P5-E5: ferramentas aprovadas tecnicamente.** Os schemas Pydantic, a ligação `bind_tools` e o loop do agente foram exercitados com modelo falso, sem rede. O agente listou elementos e trocou uma imagem pelo ID certo; uma duração inválida foi rejeitada pelo código sem alterar o plano. Casos de zoom encoberto por B-roll e encurtamento fora da frase ou da borda da palavra também foram rejeitados. A suíte completa passou; a revisão independente aprovou a etapa.
- 👀 **P5-C5: conferir o LLM real quando a interface de chat da Etapa 6 estiver pronta.** Reutilize um projeto já gerado com imagens. Peça para listar as imagens e trocar uma delas por outra busca, confira o ID e a pertinência da nova imagem na prévia antes de aplicar. Depois peça uma duração acima do limite e confirme que a ação é recusada sem alterar o vídeo. Isso exige a chave de LLM já configurada e uma chamada paga; nenhum vídeo novo em `samples/` é necessário.

### Linha do tempo e prévia pós-render (25/09/2026)

- ✅ **P5-E4: fluxo técnico validado.** Testes sintéticos cobriram marcadores dos quatro tipos, troca de imagem e remoção de B-roll, geração de prévia curta sem áudio, confirmação sem alterar quadros vizinhos, rejeição de vídeo obsoleto e desfazer da troca. A suíte completa, Ruff, doctor e build da interface passaram; a avaliação visual com mídia real segue pendente.
- 👀 **P5-C4: conferir a aba Edição com um projeto real já gerado.** Localize na linha do tempo uma imagem e um B-roll, selecione cada um e gere a prévia de troca por alternativa ou upload. Confira se o trecho exibido corresponde ao que foi dito, se o vídeo final fica igual antes de clicar em **Aplicar ao vídeo final**, e se a troca e o Desfazer aparecem no resultado. Verifique também se zooms e destaques aparecem nos tempos esperados. Pode reutilizar o projeto de P5-C2/P5-C3; nenhum vídeo novo em `samples/` é necessário.

### Histórico e desfazer/refazer pós-render (25/09/2026)

- ✅ **P5-E3: histórico aprovado tecnicamente.** Testes sintéticos confirmaram que Desfazer e Refazer restauram exatamente os bytes do MP4 e o documento do projeto após troca de imagem e B-roll. Também cobriram limite de versões, descarte do ramo de refazer, mídia original ausente, prévia pendente, falha de render e geração malsucedida com opções diferentes. A revisão independente aprovou a etapa; 563 testes passaram na suíte completa (19 de integração não executados), além do build do frontend, Ruff e doctor.
- 👀 **P5-C3: conferir desfazer/refazer com um vídeo real na interface quando puder.** Em um projeto já gerado, substitua uma imagem, assista, clique em **Desfazer** e confirme que a imagem e o vídeo voltaram ao estado anterior; clique em **Refazer** e confirme que a troca reaparece. Depois, desfaça e faça uma troca diferente para conferir que Refazer fica indisponível. Use o mesmo projeto da conferência P5-C2; não são necessários novos arquivos em `samples/`.

### Substituição pós-render (25/09/2026)

- ✅ **P5-E2: troca técnica aprovada.** A UI oferece nova busca, alternativa e upload para imagem e B-roll. Testes sintéticos trocaram cada tipo por upload, confirmaram que a nova mídia aparece no intervalo esperado e que os quadros fora dele permanecem iguais. A seleção de alternativa usa candidatos salvos, sem repetir busca. Erros de preparo deixam plano e vídeo anteriores intactos; efeitos desligados bloqueiam a troca. A revisão independente aprovou a etapa. Suíte completa: 560 testes aprovados e 19 de integração não executados; Ruff, build do frontend e doctor passaram (0 faltando).
- 👀 **P5-C2: conferir a troca na interface com um vídeo real quando puder.** Abra um projeto já gerado com imagens e B-roll ligados. Na aba **Plano criativo**, substitua uma imagem e um cutaway por arquivos seus ou por alternativas; aguarde os jobs e assista ao resultado. Confira a pertinência visual e se voz, legendas e cenas vizinhas continuam iguais. Nenhum arquivo novo em `samples/` é necessário: use um dos projetos que já têm esses efeitos.

### Render incremental pós-render (24/09/2026)

- ✅ **P5-E1: segmentação e cache aprovados tecnicamente.** Os testes sintéticos compararam quadro a quadro e amostra a amostra o resultado incremental com o render completo equivalente após alterar imagem e B-roll com transição. A troca de modo de legenda reaproveitou os trechos sem texto; elementos inativos não invalidaram cache, e o áudio coincidiu com o render legado. O job da API reutilizou todos os segmentos na segunda geração sem mudanças. Suíte completa: 556 testes aprovados, 19 de integração não executados; após os ajustes da revisão, 39 testes focados passaram. Doctor: 0 faltando. A revisão independente aprovou a etapa. A troca por UI e a avaliação em vídeo real pertencem às etapas seguintes.

### Documento de edição pós-render (24/09/2026)

- ✅ **P5-E0: documento v2 e migração concluídos.** `project.json` agora contém IDs estáveis para clipes, cortes, imagens, zooms, B-roll, destaques, crops e eventos de legenda. A API lê o plano criativo do documento; sidecars antigos só servem como cópia de compatibilidade. Dois projetos existentes foram migrados, cada um com backup `project.v1.json`; o plano importado do projeto com sidecar foi comparado integralmente e preservado. Testes sintéticos cobrem o documento novo, migração, intervalos visuais, edição por ID e invalidação após cortes. Suíte completa: 552 aprovados, 19 de integração não executados; após o ajuste final, 75 testes focados e Ruff passaram. Doctor: 0 itens faltando. O verificador independente aprovou a etapa. Não há conferência manual necessária para este critério.

### Legendas de destaque (24/09/2026)

- 👀 **P4-C2: conferir a troca entre os três modos no vídeo final.** No mesmo projeto com uma fala, gere o vídeo escolhendo cada opção do seletor: **Nenhuma**, **Legenda contínua** e **Legendas de destaque**. Confira que cada render contém apenas a modalidade selecionada, sem restos do anterior. No modo Destaque, altere permanência, tamanho e cor e veja se o resultado acompanha os ajustes. Os testes com vídeo sintético já verificaram as três trocas e a ausência de texto residual; falta sua avaliação na interface com vídeo real.
- **P4-E2, verificação técnica:** a suíte completa passou com 543 testes (19 de integração não executados). A revisão independente encontrou e motivou dois ajustes adicionais: a CLI passou a salvar o modo de legenda selecionado e a atualização da interface passou a reler as preferências do projeto. Depois deles, 77 testes focados, Ruff e o build do frontend passaram. Doctor: 0 itens faltando. A etapa permanece aberta apenas para a conferência visual P4-C2.
- 👀 **P4-C1: conferir a sincronia perceptiva com fala real e o novo posicionamento.** Assista `output/parte4_etapa1_fala_real_curta.mp4` (4 s, trecho de quatro palavras “não prestar atenção nisso”). Veja se a palavra amarela acompanha a voz, se o trecho completo fica visível por 1,2 s depois de “nisso” e se o fade parece suave. Confira se o texto pequeno no alto deixa a palestrante e a cena livres. O quadro, os tempos e as caixas de rosto foram verificados em testes; falta seu julgamento com áudio. O vídeo antigo de 11 palavras (`parte4_etapa1_fala_real.mp4`) é apenas histórico.
- **P4-E1, verificação técnica após o ajuste visual:** 540 testes aprovados, 19 testes de integração não executados, Ruff aprovado e doctor com 0 itens faltando. O planejamento real da palestra gerou três trechos de 3–4 palavras em `output/parte4_etapa1_plano_curto.json`; o `.ass` correspondente passou pela validação de tempos, emendas e rostos. O verificador independente aprovou o escopo automatizável. O antigo plano e vídeo de frases longas são históricos.
- ✅ **P4-E0: planejamento de frases de impacto concluído.** `output/parte4_etapa0_plano_destaques.json` guarda o roteiro de demonstração e os três destaques escolhidos pelo LLM real: descoberta da perda de água, decisão de medir e benefício para as famílias. A revisão independente considerou as escolhas pertinentes e confirmou a correção que rejeita frases que atravessam cortes internos. Texto literal, bordas, espaçamento e ausência de sobreposição passaram nos testes; suíte completa: 530 aprovados; doctor: 0 faltando.

### Cutaways de B-roll (24/09/2026)

- 👀 **P3-C4: conferir a integração na interface quando puder.** Na interface, ative B-roll, ajuste intervalo e transição, rode "Preparar B-roll", assista cada sugestão na aba "Plano criativo", aprove ou desative um vídeo e gere o resultado. Confira também `output/parte3_etapa4_tres_clipes.mp4`: é uma demonstração técnica sintética de 12 s com três clipes; o azul é o B-roll no primeiro, o zoom fica no segundo e a imagem verde no terceiro. O vídeo de apoio sugerido para o terceiro clipe é descartado porque conflita com a imagem. Os testes automáticos conferem os 360 quadros, as contagens e que desligar B-roll preserva imagens e zooms. Suíte completa: 524 testes aprovados; build do frontend e doctor passaram (0 faltando); verificador independente aprovou os critérios automatizáveis.
- 👀 **P3-C3: conferir a prévia ligada ao roteiro.** Veja `output/parte3_etapa3_preview_broll.json`, o clipe da colheita `output/parte3_etapa1_colheita.mp4` e o novo clipe da torra `output/parte3_etapa3_torra.mp4`. Confirme se ambos ilustram as frases de café listadas no JSON. A API permite aprovar, trocar a query e remover antes do render; o teste automatizado verificou que remover mostra a câmera e que trocar a query e re-renderizar não chama o LLM. A suíte passou com 522 testes; doctor: 0 faltando. O verificador independente aprovou a parte automatizável e a correção que oculta prévias obsoletas. A interface de aprovação agora está na aba "Plano criativo".
- ✅ **P3-C2: transições aprovadas para avançar pelo usuário.** O usuário aprovou a suavidade do crossfade, pediu mais opções e, após receber prévias de deslizamento e varredura, pediu para seguir à etapa seguinte. As prévias de 7 s têm 1080x1920/30 fps, 210 quadros e áudio contínuo idêntico; a suíte passou com 521 testes, doctor 0 faltando e o verificador independente não encontrou quadros pretos ou travados. O catálogo ampliado de efeitos foi planejado na Parte 6.
- ✅ **P3-C1: clipe buscado aprovado pelo usuário.** `output/parte3_etapa1_colheita.mp4` mostra colheita de café em enquadramento vertical. Duração, formato e ausência de áudio foram conferidos automaticamente. A suíte passou com 511 testes, o doctor teve 0 faltando e o verificador independente aprovou a parte automatizável.
- ✅ **P3-E0: planejamento aprovado pelo usuário.** O LLM real escolheu duas frases inteiras do roteiro de café para cutaways (colheita e torra), sem usar a frase já reservada a uma imagem. O código valida bordas exatas das palavras, emendas entre clipes e entre trechos cortados, duração de 1,5–4 s, densidade de 8 s e retorno à câmera. A suíte completa passou com 502 testes; doctor, ruff e verificador independente aprovaram a parte automatizável.

### Estabilizador de vídeo (24/09/2026)

- ✅ **P2-C0: prova de conceito aprovada pelo usuário.** O tremor diminuiu perceptivelmente no vídeo `output/parte2_etapa0_palestra_estabilizada.mp4`. O doctor confirmou `vidstabdetect` e `vidstabtransform`; a saída preservou o áudio e a duração de 69,166667 s.
- ✅ **P2-C1: estabilizador básico aprovado pelo usuário.** O resultado `output/parte2_etapa1_palestra_medio.mp4` ficou bom na avaliação visual/auditiva. Nove combinações sintéticas (três intensidades × três níveis) reduziram o deslocamento em pelo menos 10% e não produziram faixas pretas; o áudio real permaneceu bit a bit igual ao original.
- ✅ **P2-E2: CLI standalone verificada.** `python -m src.video.stabilize` aceita nível de suavização e crop, mostra o progresso das duas passadas e retorna erros claros para entrada inválida ou `vidstab` ausente. Um teste com vídeo sintético de 150 s terminou em 27,89 s sem travar e preservou a duração. A suíte rápida passou com 474 testes; o verificador independente aprovou a etapa.
- ✅ **P2-C3: fallback OpenCV aprovado pelo usuário.** O vídeo `output/parte2_etapa3_palestra_opencv.mp4` ficou bom na avaliação visual/auditiva. A seleção automática e o cache específico do motor passaram nos testes; no vídeo real, os 2.073 quadros e o áudio AAC foram preservados.
- ✅ **P2-E4: integração verificada.** O projeto guarda opção e nível, a API e a interface os reaproveitam, e rastreio/render recebem o vídeo estabilizado por clipe sem alterar a fonte da transcrição. O fallback OpenCV também gerou vídeo final no teste. A suíte completa passou com 482 testes; os testes novos passaram após o último ajuste, o build passou e o doctor teve 0 faltando. O verificador independente aprovou a parte automatizável.
- ✅ **P2-C4: comparação do rastreio aprovada pelo usuário.** `output/parte2_etapa4_comparacao_rosto.mp4` mostrou o rosto mais estável com a opção ligada; a etapa 4 foi concluída.
- ✅ **P2-E5: ajuste fino e testes concluídos.** Três clipes sintéticos com tremor leve, médio e forte foram medidos por variância do deslocamento entre quadros. Vidstab e OpenCV reduziram o tremor em pelo menos 99% nos seis casos, acima da margem exigida de 30%. `STABILIZE_SMOOTHING` e `STABILIZE_CROP_PERCENT` foram ligados aos padrões da CLI e do editor; o cache inclui o crop efetivo. O README cobre os dois modos de uso e o fallback. Suíte completa: 493 testes passaram; doctor: 0 faltando; build do frontend e verificador independente aprovaram.

### Otimizador de áudio (23/09/2026)

- ✅ **P1-C0 e P1-C1: a limpeza soou bem.** O usuário ouviu os pares `output/parte1_etapa0_*.wav` (só denoise) e `output/parte1_etapa1_*.wav` (cadeia completa, padrão e `aggressiveness=1`) e aprovou: sem ruído e sem voz robótica. Os padrões ficam como estão (`aggressiveness=0,5`, alvo de −16 LUFS).

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
