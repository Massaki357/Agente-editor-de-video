# Plano de Implementação --- Caption Style Engine + Transition Engine

> **Projeto:** Editor de vídeo existente em Python + FFmpeg + OpenCV +
> ASS\
> **Objetivo:** adicionar diversidade de estilos/animações de legenda e
> transições mais elaboradas sem reescrever o pipeline atual.

## Instruções obrigatórias para o Claude Code

Este arquivo é também o **controle de progresso da implementação**.

-   Trabalhe **uma etapa por vez**, na ordem definida abaixo.
-   **NÃO avance automaticamente para a próxima etapa.**
-   Ao terminar uma etapa, execute as validações/testes previstos,
    atualize este arquivo marcando os itens concluídos de `[ ]` para
    `[x]` e escreva um resumo em **Registro da etapa**.
-   Depois disso, **PARE** e apresente ao usuário:
    1.  o que foi alterado;
    2.  quais arquivos foram criados/modificados;
    3.  quais testes foram executados e seus resultados;
    4.  como o usuário pode verificar manualmente;
    5.  problemas/limitações encontrados.
-   Só continue quando o usuário autorizar explicitamente a próxima
    etapa.
-   Se encontrar uma decisão arquitetural importante não prevista aqui,
    **não decida silenciosamente**: documente a questão e pare para
    aprovação.
-   Preserve o comportamento atual do editor. Não reescreva partes
    funcionais apenas por preferência arquitetural.
-   Não remova funcionalidades existentes.
-   Faça mudanças pequenas, rastreáveis e reversíveis.
-   Antes de modificar código, entenda como o projeto realmente
    funciona. Os nomes de pastas sugeridos neste documento são
    **conceituais**; adapte-os à arquitetura real.
-   Sempre que possível, mantenha compatibilidade com projetos/edições
    já existentes.

------------------------------------------------------------------------

# Visão do resultado desejado

A arquitetura final deve manter o pipeline existente como base:

``` text
                         Editor / LLM
                              │
                       comandos de edição
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
      Caption Style Engine            Transition Engine
              │                               │
        ASS / FFmpeg                   FFmpeg / OpenCV
              │                               │
              └───────────────┬───────────────┘
                              ▼
                      Pipeline existente
                              │
                           FFmpeg
                              │
                              ▼
                         Vídeo final
```

O objetivo **não é introduzir Remotion agora**. Se durante a análise
ficar evidente que um efeito específico justificaria um renderer
complementar, apenas documente a possibilidade; não adicione uma nova
stack sem autorização.

------------------------------------------------------------------------

# ETAPA 1 --- Auditoria da arquitetura atual

## Objetivo

Entender completamente o pipeline antes de alterar qualquer
implementação.

## Tarefas

-   [x] Identificar o ponto de entrada do processamento/renderização.
-   [x] Mapear o fluxo completo desde os parâmetros de edição até o
    arquivo final.
-   [x] Localizar toda a implementação relacionada a FFmpeg.
-   [x] Localizar toda a implementação relacionada a OpenCV.
-   [x] Localizar geração e renderização das legendas ASS.
-   [x] Identificar como timestamps e sincronização são tratados.
-   [x] Identificar como funciona o render incremental/por segmentos.
-   [x] Identificar o sistema de cache.
-   [x] Identificar como zoom, tracking facial e `warpPerspective`
    interagem com o render.
-   [x] Identificar como cortes/clipes são representados internamente.
-   [x] Verificar se já existe alguma implementação de transições.
-   [x] Verificar onde estilos de legenda estão acoplados ao restante do
    sistema.
-   [x] Identificar testes existentes relacionados ao pipeline.
-   [x] Registrar riscos de regressão.
-   [x] Propor, sem implementar ainda, os pontos de integração para
    Caption Style Engine e Transition Engine.

## Entregável

Criar no próprio projeto uma documentação curta, por exemplo:

`docs/video-engine-architecture.md`

Ela deve mostrar o pipeline real encontrado no código e os pontos de
extensão recomendados.

### Registro da etapa

## **Arquivos alterados:**

- `docs/video-engine-architecture.md` (novo) --- auditoria completa: mapa do
  pipeline real, pontos de uso de FFmpeg/OpenCV, os dois geradores de ASS
  existentes, timestamps/sincronização, render incremental e cache, zoom/
  tracking, representação de cortes/clipes, transições existentes (B-roll),
  inventário de testes, riscos de regressão e pontos de integração propostos
  (sem implementação) para o Caption Style Engine e o Transition Engine.
- `PLANO_CAPTIONS_TRANSITIONS_CLAUDE_CODE.md` --- checklist da Etapa 1 e este
  registro.

## **Testes executados:**

- `cd API && uv run pytest -q` --- **619 passed, 19 deselected** (mesma
  contagem da baseline antes desta etapa; nenhum código de produção foi
  alterado, é uma checagem de sanidade, não uma prova de mudança).

## **Observações/riscos:**

- O plano cita `cv2.warpPerspective`; o código só usa `cv2.warpAffine`
  (translação + escala) em `render.py` (zoom) e `video/opencv_fallback.py`
  (estabilização). Não existe transformação de perspectiva em lugar nenhum.
- Já existe um motor de transições em produção (`src/broll/transitions.py` +
  `src/broll/catalog.py`, 7 presets, catálogo, prévias, validação e testes),
  mas ele é **inteiramente escopado a cutaways de B-roll** dentro de um único
  trecho mantido. Não há hoje um mecanismo de transição entre clipes/trechos
  reais da timeline --- a emenda (`TimeMap.seams()`) é tratada como fronteira
  dura em vários pontos independentes (`_plan_cutaways`, validação de
  destaques, suposição de sincronia A/V do render). Estender o motor atual
  para cobrir emendas reais é uma decisão arquitetural explícita, não uma
  extensão trivial --- ver §9 e §12 de `docs/video-engine-architecture.md`.
  **Isto precisa de decisão do usuário antes da Etapa 5.**
- Não existe uma "Caption Style Engine": são dois geradores de `.ass`
  independentes (`src/captions.py` para legenda contínua,
  `src/highlight_captions/` para legenda de destaque), cada um com sua
  própria classe de estilo e lógica de layout/revelação, compartilhando só
  helpers privados de baixo nível. Uma nova etapa precisa decidir se cria um
  registry que envolve os dois (preservando-os como estão) ou se generaliza
  um deles --- ver §4 e §12 do documento.
- Boa notícia para o risco de cache: o render incremental
  (`src/editing/incremental_render.py::_hash_segment`) já invalida por
  conteúdo bruto do `.ass` e da configuração de transição por cutaway, então
  novos presets/estilos tendem a invalidar o cache corretamente **desde que
  continuem produzindo um único `.ass` por render** e que qualquer campo novo
  de configuração entre nesse hash (esquecer um campo faria o cache não
  invalidar quando deveria --- o pior tipo de bug aqui).
- `xfade` não está na checagem proativa do `doctor.py`
  (`REQUIRED_FFMPEG_FILTERS`); a ausência de um efeito específico já cai para
  `hard_cut` com aviso, e uma biblioteca maior de efeitos deve preservar esse
  padrão de fallback.
- Nenhuma funcionalidade nova foi implementada nesta etapa, conforme a regra
  de parada obrigatória.

### ⛔ PARADA OBRIGATÓRIA 1

Não implemente nenhuma nova funcionalidade nesta etapa. Atualize os
checks, apresente a auditoria ao usuário e aguarde autorização.

------------------------------------------------------------------------

# ETAPA 2 --- Fundação do Caption Style Engine

## Objetivo

Desacoplar **conteúdo da legenda**, **estilo visual** e **animação**,
preservando o comportamento atual como preset legado/default.

## Requisitos

A API interna deve caminhar para algo conceitualmente semelhante a:

``` python
add_captions(
    segments=segments,
    style="default",
    animation="none",
    position="bottom"
)
```

Não é obrigatório usar exatamente essa assinatura caso não combine com a
arquitetura existente.

## Tarefas

-   [x] Criar uma abstração central para estilos de legenda.
-   [x] Criar registro/registry de presets.
-   [x] Transformar o estilo atual em um preset `default`/`legacy`.
-   [x] Garantir que vídeos existentes continuem usando o mesmo visual
    quando nenhum novo estilo for informado.
-   [x] Separar configuração visual da lógica de sincronização.
-   [x] Definir configuração para fonte, tamanho, peso, cor, outline,
    sombra, background, alinhamento e posição.
-   [x] Preparar suporte a configuração de animação sem implementar
    todos os efeitos ainda.
-   [x] Adicionar validação/fallback para preset inexistente.
-   [x] Criar testes unitários ou equivalentes para geração de
    ASS/configuração.
-   [x] Renderizar um pequeno vídeo de regressão usando o preset legado.
-   [x] Comparar o resultado com o comportamento anterior.

## Critério de aceite

O sistema deve possuir a nova arquitetura de presets **sem alterar
visualmente o comportamento padrão atual**.

### Registro da etapa

## **Arquivos alterados:**

- `API/src/caption_presets.py` (novo) --- abstração central: `CaptionPreset` (estilo +
  animação), registro `_REGISTRY` com o preset `"default"` (idêntico ao
  `CaptionStyle()` que já existia), `resolve_preset` (fallback + aviso de log para nome
  desconhecido) e `resolved_style` (override explícito vence; senão usa o preset).
- `API/src/captions.py` --- `CaptionStyle` ganhou três campos novos, todos com o valor
  padrão igual ao que estava fixo no código antes (`negrito: bool = True`,
  `fundo: bool = False`, `alinhamento: "inferior"|"centro"|"superior" = "inferior"`);
  `build_ass` lê esses campos em vez de valores fixos (Bold/BorderStyle/Alignment do
  `.ass`); `box()` calcula a área ocupada para os três alinhamentos (antes só suportava
  a base da tela).
- `API/src/pipeline.py` --- `PipelineOptions` ganhou `preset_legenda: str = "default"`;
  `estilo_legenda` passou de `CaptionStyle` para `CaptionStyle | None = None` (`None`
  = "sem override, usa o preset"). `render_project`/`make_captions` resolvem o estilo
  efetivo por `caption_presets.resolved_style` em vez de usar `estilo_legenda`
  diretamente.
- `API/tests/test_caption_presets.py` (novo) --- 19 testes: registro/fallback,
  precedência override-vs-preset, os três campos visuais novos e sua codificação no
  `.ass`, geometria de `box()` nos três alinhamentos, compatibilidade do
  `PipelineOptions` (dict parcial continua funcionando) e o vídeo de regressão.

## **Testes executados:**

- `cd API && uv run pytest -q` --- **638 passed, 19 deselected** (619 da baseline +
  19 novos desta etapa; nenhum teste existente mudou).
- `cd API && uv run ruff check .` --- sem problemas.
- `cd API && uv run python -m src.doctor` --- 34 itens, 0 faltando (1 aviso
  pré-existente e não relacionado: `PIXABAY_API_KEY` vazia).

## **Resultado da regressão:**

- Todos os testes que já travavam o comportamento visual anterior continuam passando
  sem alteração: a linha `Style: Legenda` gerada para o preset `default` tem
  exatamente os mesmos valores de antes (`Bold=-1`, `BorderStyle=1`, `Alignment=2`,
  `MarginV=520`, tamanho de fonte, cores), os tempos continuam arredondados para baixo
  na emenda, a legenda continua sem atravessar emenda entre clipes, e o vídeo queimado
  continua legível em fundo claro e escuro.
- Vídeo de regressão novo (`test_regression_video_with_the_legacy_preset_still_burns_
  readable_captions`): gera um clipe sintético preto de 2s, resolve o estilo pelo
  registry (`resolve_preset("default").style`, não mais o `CaptionStyle()` direto),
  queima a legenda com `render_timeline` e confirma pixel a pixel que a palavra atual
  aparece em amarelo e o texto em branco sobre o fundo preto --- a mesma asserção do
  teste que já existia antes desta etapa para o caminho antigo.
  Nenhum efeito de animação foi implementado (`animacao` só aceita `"none"` hoje); os
  três novos eixos visuais (`negrito`, `fundo`, `alinhamento`) existem no
  `CaptionStyle` e são testados isoladamente, mas nenhum preset além do `default` os
  usa ainda --- isso é o trabalho da Etapa 3.

### ⛔ PARADA OBRIGATÓRIA 2

Mostre ao usuário a arquitetura criada e o vídeo/teste de regressão.
Aguarde autorização.

------------------------------------------------------------------------

# ETAPA 3 --- Biblioteca inicial de estilos de legenda

## Objetivo

Adicionar diversidade visual real às legendas.

## Presets iniciais

Implemente pelo menos:

-   [x] `clean`
-   [x] `bold`
-   [x] `minimal`
-   [x] `cinematic`
-   [x] `social`
-   [x] `karaoke`

Os nomes podem ser adaptados se o projeto já possuir convenção própria.

## Recursos

-   [x] Variação de tipografia.
-   [x] Tamanho.
-   [x] Peso.
-   [x] Contorno.
-   [x] Sombra.
-   [x] Background quando aplicável.
-   [x] Margens.
-   [x] Posicionamento.
-   [x] Cores configuráveis.
-   [x] Destaque de palavra quando suportado pelo preset.
-   [x] Quebra de linha adequada.
-   [x] Safe area para vídeos verticais e horizontais.
-   [x] Preservar caracteres acentuados/Unicode usados pelo projeto.

## Importante

Não copie identidade visual proprietária específica de outro editor. Os
presets devem ser estilos próprios e genéricos.

## Validação visual

Gerar um vídeo de demonstração contendo o mesmo trecho de fala
renderizado com cada preset.

### Registro da etapa

## **Presets implementados:**

`clean`, `bold`, `minimal`, `cinematic`, `social` e `karaoke`. Os seis
mantêm o destaque sincronizado por palavra e variam tipografia, tamanho,
peso, contorno, sombra, fundo, agrupamento, margens, posição e cores.
O preset `default` continua idêntico ao comportamento anterior.

## **Arquivos alterados:**

`API/src/caption_presets.py`, `API/src/captions.py`,
`API/src/caption_style_demo.py`, `API/tests/test_caption_style_library.py`
e as fontes/licenças em `API/fonts/`.

## **Teste visual:**

`output/caption_styles_etapa3.mp4` contém 18 segundos, 540 quadros a 30 fps,
vídeo H.264 e áudio AAC. O mesmo texto e a mesma cena são repetidos nos seis
presets; `output/caption_styles_etapa3.json` informa a ordem e os intervalos.
A checagem humana está registrada em `testes-pendentes.md`.

### ⛔ PARADA OBRIGATÓRIA 3

Apresente os presets ao usuário para aprovação visual antes de
implementar animações avançadas.

------------------------------------------------------------------------

# ETAPA 4 --- Animações de legenda

## Objetivo

Adicionar movimento e sincronização dinâmica sem comprometer
legibilidade ou performance.

## Animações desejadas

-   [ ] `fade`
-   [ ] `pop`
-   [ ] `word_highlight`
-   [ ] `word_by_word`
-   [ ] `karaoke`
-   [ ] `bounce` sutil

## Requisitos técnicos

-   [ ] Reaproveitar ASS sempre que ele oferecer resultado adequado.
-   [ ] Não introduzir processamento frame-a-frame desnecessário.
-   [ ] Manter sincronização precisa.
-   [ ] Evitar flicker.
-   [ ] Permitir intensidade/duração configuráveis quando fizer sentido.
-   [ ] Permitir combinar `style + animation`.
-   [ ] Criar fallback quando uma combinação não for compatível.
-   [ ] Medir impacto aproximado no tempo de render.
-   [ ] Testar vídeos 16:9 e 9:16.

Exemplo conceitual:

``` python
caption_config = {
    "style": "social",
    "animation": "word_highlight",
    "position": "bottom"
}
```

### Registro da etapa

## **Animações implementadas:**

## **Impacto no render:**

## **Problemas encontrados:**

### ⛔ PARADA OBRIGATÓRIA 4

Mostre amostras das animações e aguarde escolha/aprovação do usuário.

------------------------------------------------------------------------

# ETAPA 5 --- Fundação do Transition Engine

## Objetivo

Criar uma camada única para transições sem quebrar cortes e render
incremental existentes.

## API conceitual

``` python
transition(
    clip_a=...,
    clip_b=...,
    type="fade",
    duration=0.4,
    parameters={}
)
```

## Tarefas

-   [ ] Mapear exatamente onde a transição deve entrar no pipeline.
-   [ ] Criar abstração `Transition`.
-   [ ] Criar registry de transições.
-   [ ] Criar validação de duração.
-   [ ] Tratar clips menores que a duração solicitada.
-   [ ] Tratar diferenças de resolução/FPS/formato quando necessário.
-   [ ] Garantir sincronização de áudio durante a transição.
-   [ ] Verificar interação com cache.
-   [ ] Verificar interação com render incremental.
-   [ ] Preservar corte seco como comportamento default.
-   [ ] Criar testes automatizados.
-   [ ] Testar concatenação sem transição para comprovar ausência de
    regressão.

### Registro da etapa

## **Arquitetura implementada:**

## **Arquivos alterados:**

## **Testes:**

### ⛔ PARADA OBRIGATÓRIA 5

Apresente a arquitetura e o teste de regressão. Não implemente a
biblioteca avançada ainda.

------------------------------------------------------------------------

# ETAPA 6 --- Transições básicas com FFmpeg

## Objetivo

Construir uma biblioteca confiável de transições usando recursos nativos
do FFmpeg quando possível.

## Transições

-   [ ] Fade
-   [ ] Dissolve
-   [ ] Wipe Left
-   [ ] Wipe Right
-   [ ] Slide Left
-   [ ] Slide Right
-   [ ] Circle Open
-   [ ] Circle Close
-   [ ] Pixelize
-   [ ] Radial

## Requisitos

-   [ ] Utilizar `xfade` ou filtros equivalentes quando apropriado.
-   [ ] Manter áudio corretamente sincronizado.
-   [ ] Configurar duração.
-   [ ] Configurar direção onde aplicável.
-   [ ] Evitar re-encode intermediário desnecessário.
-   [ ] Criar um vídeo demo com todas as transições identificadas pelo
    nome.
-   [ ] Medir aproximadamente o custo adicional de render.

### Registro da etapa

## **Transições implementadas:**

## **Performance:**

## **Limitações:**

### ⛔ PARADA OBRIGATÓRIA 6

Apresente o vídeo demo e aguarde aprovação antes das transições
avançadas.

------------------------------------------------------------------------

# ETAPA 7 --- Transições avançadas

## Objetivo

Criar transições visualmente mais fortes usando composição de FFmpeg e,
somente quando necessário, OpenCV.

## Alvos iniciais

-   [ ] Zoom In
-   [ ] Zoom Out
-   [ ] Whip Left
-   [ ] Whip Right
-   [ ] Blur Transition
-   [ ] Flash
-   [ ] Shake
-   [ ] RGB Split / Chromatic
-   [ ] Glitch simples

## Princípios

1.  Preferir FFmpeg.
2.  Usar OpenCV somente quando trouxer benefício claro.
3.  Evitar render frame-a-frame em Python quando FFmpeg conseguir
    executar o efeito.
4.  Não duplicar frames do vídeo inteiro apenas para produzir uma
    transição curta.
5.  Manter os efeitos parametrizáveis.

Exemplo:

``` python
transition(
    type="whip",
    duration=0.35,
    parameters={
        "direction": "right",
        "motion_blur": 0.7,
        "intensity": 0.8
    }
)
```

## Validação

-   [ ] Criar demo.
-   [ ] Verificar artefatos.
-   [ ] Verificar frames pretos.
-   [ ] Verificar áudio.
-   [ ] Verificar FPS.
-   [ ] Verificar resoluções 16:9 e 9:16.
-   [ ] Medir performance.
-   [ ] Identificar quais efeitos são adequados para produção.

### Registro da etapa

## **Transições aprovadas tecnicamente:**

## **Transições experimentais:**

## **Performance:**

### ⛔ PARADA OBRIGATÓRIA 7

Mostre cada efeito ao usuário. Aguarde decisão sobre quais devem
permanecer disponíveis no produto.

------------------------------------------------------------------------

# ETAPA 8 --- Presets e interface unificada para LLM

## Objetivo

Fazer a LLM controlar os recursos sem conhecer detalhes internos de ASS,
FFmpeg ou OpenCV.

## Princípio

A LLM deve dizer **o que deseja**, enquanto o engine decide **como
executar**.

Evitar expor comandos FFmpeg arbitrários à LLM.

## Modelo conceitual

``` json
{
  "captions": {
    "style": "social",
    "animation": "word_highlight",
    "position": "bottom"
  },
  "transitions": [
    {
      "between": ["clip_1", "clip_2"],
      "type": "whip",
      "duration": 0.35,
      "parameters": {
        "direction": "right",
        "intensity": 0.8
      }
    }
  ]
}
```

## Tarefas

-   [ ] Definir schema estruturado.
-   [ ] Definir valores permitidos.
-   [ ] Definir defaults.
-   [ ] Validar parâmetros.
-   [ ] Impedir valores perigosos/inválidos.
-   [ ] Retornar mensagens de erro compreensíveis para a LLM.
-   [ ] Expor catálogo de estilos disponíveis.
-   [ ] Expor catálogo de animações disponíveis.
-   [ ] Expor catálogo de transições disponíveis.
-   [ ] Permitir consulta às capacidades do engine.
-   [ ] Criar exemplos de chamadas.
-   [ ] Criar testes com configurações válidas.
-   [ ] Criar testes com configurações inválidas.

### Registro da etapa

## **Schema criado:**

## **Integração:**

## **Testes:**

### ⛔ PARADA OBRIGATÓRIA 8

Apresente ao usuário o contrato final que a LLM utilizará e aguarde
aprovação.

------------------------------------------------------------------------

# ETAPA 9 --- Integração com a interface do editor

## Objetivo

Disponibilizar as novas funcionalidades na interface atual sem
prejudicar o fluxo existente.

## Legendas

-   [ ] Seletor de estilo.
-   [ ] Seletor de animação.
-   [ ] Preview quando tecnicamente viável.
-   [ ] Controles relevantes sem expor complexidade desnecessária.
-   [ ] Default compatível com projetos antigos.

## Transições

-   [ ] Seletor entre dois clips/segmentos.
-   [ ] Escolha do tipo.
-   [ ] Duração.
-   [ ] Parâmetros específicos quando necessários.
-   [ ] Opção `Nenhuma / Corte seco`.
-   [ ] Preview quando tecnicamente viável.

## UX

Não adicione dezenas de controles simultaneamente. Organize presets e
opções avançadas de maneira progressiva.

### Registro da etapa

## **Interface alterada:**

## **Fluxo de uso:**

## **Pendências:**

### ⛔ PARADA OBRIGATÓRIA 9

Apresente a interface ao usuário e aguarde teste manual/aprovação.

------------------------------------------------------------------------

# ETAPA 10 --- Testes finais, performance e documentação

## Objetivo

Validar que os novos recursos não comprometeram estabilidade ou
performance do editor.

## Matriz mínima

-   [ ] 1920x1080 horizontal.
-   [ ] 1080x1920 vertical.
-   [ ] Vídeo curto.
-   [ ] Vídeo com vários segmentos.
-   [ ] Vídeo com tracking facial.
-   [ ] Vídeo com zoom existente.
-   [ ] Legenda sem animação.
-   [ ] Legenda com animação.
-   [ ] Transição básica.
-   [ ] Transição avançada.
-   [ ] Várias transições no mesmo vídeo.
-   [ ] Legenda + transição + zoom/tracking simultaneamente.
-   [ ] Áudio sincronizado.
-   [ ] Cache.
-   [ ] Render incremental.
-   [ ] Projeto antigo/compatibilidade retroativa.

## Performance

Registrar, quando possível:

``` text
Cenário                      Antes       Depois
------------------------------------------------
Render padrão                ____        ____
Legenda padrão               ____        ____
Legenda animada              N/A         ____
Transição básica             N/A         ____
Transição avançada           N/A         ____
```

Não faça otimizações grandes sem evidência de gargalo.

## Documentação

-   [ ] Atualizar arquitetura.
-   [ ] Documentar presets de legenda.
-   [ ] Documentar animações.
-   [ ] Documentar transições.
-   [ ] Documentar parâmetros.
-   [ ] Documentar integração com LLM.
-   [ ] Documentar como criar novos presets.
-   [ ] Documentar como criar novas transições.
-   [ ] Documentar limitações conhecidas.

### Registro da etapa

## **Resultado dos testes:**

## **Benchmarks:**

## **Limitações conhecidas:**

### ⛔ PARADA OBRIGATÓRIA 10

Apresente o relatório final ao usuário. Não faça refactors adicionais
sem nova autorização.

------------------------------------------------------------------------

# Checklist geral

-   [x] Etapa 1 --- Auditoria
-   [x] Etapa 2 --- Fundação Caption Engine
-   [x] Etapa 3 --- Estilos de legenda
-   [ ] Etapa 4 --- Animações de legenda
-   [ ] Etapa 5 --- Fundação Transition Engine
-   [ ] Etapa 6 --- Transições básicas
-   [ ] Etapa 7 --- Transições avançadas
-   [ ] Etapa 8 --- Interface para LLM
-   [ ] Etapa 9 --- Integração com editor
-   [ ] Etapa 10 --- Testes e documentação

------------------------------------------------------------------------

# Regra final de execução

**Você está autorizado inicialmente SOMENTE a executar a ETAPA 1.**

Ao concluir uma etapa:

1.  atualize este `.md`;
2.  marque `[x]` apenas no que realmente foi concluído;
3.  preencha o registro da etapa;
4.  informe o usuário;
5.  **PARE**;
6.  espere uma mensagem explícita como **"pode continuar para a etapa
    2"**.

Nunca interprete silêncio, resultado de teste ou uma pergunta do usuário
como autorização automática para avançar.
