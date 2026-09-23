import { useEffect, useRef, useState, type DragEvent, type FormEvent, type KeyboardEvent } from 'react'
import { fmtNum, fmtSeg, urlDoClipe, type ClipOut } from '../api'

interface Props {
  clipes: ClipOut[]
  /** job ativo ou alteração em andamento: trava reordenar, remover e importar */
  bloqueado: boolean
  /** texto da operação em andamento ('enviando 3 vídeo(s)…'), se houver */
  ocupado: string | null
  /** arquivo do clipe aberto no palco */
  selecionado: string | null
  onSelecionar: (clip: ClipOut) => void
  onRemover: (clip: ClipOut) => void
  /** nova ordem, pelos índices atuais (o que `PUT /clips/order` espera) */
  onReordenar: (ordem: number[]) => void
  onImportar: (pasta: string) => void
  onEnviar: (arquivos: File[]) => void
}

const DICA_BLOQUEIO = 'há um job em andamento: espere terminar ou cancele'

/** Faixa horizontal de clipes: seleção, reordenação (arrastar ou setas) e remoção. */
export default function ClipStrip({
  clipes,
  bloqueado,
  ocupado,
  selecionado,
  onSelecionar,
  onRemover,
  onReordenar,
  onImportar,
  onEnviar,
}: Props) {
  const [pasta, setPasta] = useState('')
  const [formPasta, setFormPasta] = useState(false)
  const [arrastando, setArrastando] = useState<number | null>(null) // posição do cartão arrastado
  const [alvo, setAlvo] = useState<number | null>(null) // posição de inserção (0..n)
  const [focar, setFocar] = useState<string | null>(null) // cartão a focar após mover pelo teclado
  const [anuncio, setAnuncio] = useState('') // nova posição, para o leitor de tela
  const inputArquivos = useRef<HTMLInputElement>(null)
  const cartoes = useRef(new Map<string, HTMLLIElement>())

  // devolve o foco ao cartão movido pelo teclado (o React mantém o nó pela key)
  useEffect(() => {
    if (!focar) return
    cartoes.current.get(focar)?.focus()
    setFocar(null)
  }, [focar])

  /** Aplica uma nova ordem tirando o cartão de `de` e inserindo antes da posição `insercao`. */
  const mover = (de: number, insercao: number) => {
    const ordem = clipes.map((_, k) => k)
    const [movido] = ordem.splice(de, 1)
    ordem.splice(insercao > de ? insercao - 1 : insercao, 0, movido)
    if (ordem.every((v, k) => v === k)) return
    onReordenar(ordem)
  }

  const moverPasso = (i: number, delta: -1 | 1) => {
    const destino = i + delta
    if (destino < 0 || destino >= clipes.length) return
    setFocar(clipes[i].arquivo)
    // aria-live: sem isto quem usa leitor de tela não sabe para onde o clipe foi
    setAnuncio(`${clipes[i].nome} agora é o clipe ${destino + 1} de ${clipes.length}`)
    mover(i, delta === 1 ? destino + 1 : destino)
  }

  const aoArrastarSobre = (ev: DragEvent<HTMLLIElement>, i: number) => {
    if (arrastando === null) return
    ev.preventDefault()
    ev.dataTransfer.dropEffect = 'move'
    const r = ev.currentTarget.getBoundingClientRect()
    setAlvo(ev.clientX < r.left + r.width / 2 ? i : i + 1)
  }

  const soltar = (ev: DragEvent<HTMLElement>) => {
    ev.preventDefault()
    // o cartão e a trilha tratam o drop; sem isto a reordenação seria aplicada duas vezes
    ev.stopPropagation()
    if (arrastando !== null && alvo !== null) mover(arrastando, alvo)
    setArrastando(null)
    setAlvo(null)
  }

  const teclado = (ev: KeyboardEvent<HTMLLIElement>, c: ClipOut, i: number) => {
    if (ev.key === 'Enter' || ev.key === ' ') {
      ev.preventDefault()
      onSelecionar(c)
    } else if ((ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') && !bloqueado) {
      ev.preventDefault()
      moverPasso(i, ev.key === 'ArrowLeft' ? -1 : 1)
    }
  }

  const importar = (ev: FormEvent) => {
    ev.preventDefault()
    if (pasta.trim()) {
      onImportar(pasta.trim())
      setPasta('')
      setFormPasta(false)
    }
  }

  return (
    <section className="faixa" aria-label="Clipes" onDragOver={(e) => arrastando !== null && e.preventDefault()}>
      <div className="faixa-topo">
        <h2 className="secao">Clipes ({clipes.length})</h2>
        <span className="suave">arraste (ou use ← →) para reordenar</span>
        <p className="oculto" aria-live="polite">
          {anuncio}
        </p>
        {bloqueado && !ocupado && <span className="texto-aviso">{DICA_BLOQUEIO}</span>}
        <div className="faixa-acoes" title={bloqueado ? DICA_BLOQUEIO : undefined}>
          {ocupado && <span className="texto-aviso">{ocupado}</span>}
          <button
            type="button"
            onClick={() => setFormPasta((v) => !v)}
            disabled={bloqueado}
            title={bloqueado ? DICA_BLOQUEIO : 'importar todos os vídeos de uma pasta local'}
          >
            Abrir pasta
          </button>
          <label className={`botao${bloqueado ? ' desabilitado' : ''}`} title={bloqueado ? DICA_BLOQUEIO : undefined}>
            Enviar
            <input
              ref={inputArquivos}
              type="file"
              multiple
              accept=".mp4,.mov,.mkv"
              className="oculto"
              disabled={bloqueado}
              onChange={(e) => {
                const arquivos = Array.from(e.target.files ?? [])
                if (arquivos.length > 0) onEnviar(arquivos)
                if (inputArquivos.current) inputArquivos.current.value = ''
              }}
            />
          </label>
        </div>
      </div>

      {formPasta && (
        <form className="linha" onSubmit={importar}>
          <label className="oculto" htmlFor="pasta-local">
            Pasta local com os vídeos
          </label>
          <input
            id="pasta-local"
            className="largo"
            value={pasta}
            autoFocus
            placeholder="caminho absoluto ou relativo à raiz do projeto (ex.: samples)"
            onChange={(e) => setPasta(e.target.value)}
            onKeyDown={(e) => e.key === 'Escape' && setFormPasta(false)}
          />
          <button type="submit" disabled={bloqueado || !pasta.trim()}>
            Importar
          </button>
        </form>
      )}

      {clipes.length === 0 ? (
        <p className="suave">Nenhum clipe. Use "Abrir pasta" ou "Enviar".</p>
      ) : (
        <ol className="trilha-clipes" onDrop={soltar}>
          {clipes.map((c, i) => {
            const aberto = selecionado === c.arquivo
            const classes = [
              'cartao-clipe',
              aberto ? 'aberto' : '',
              arrastando === i ? 'arrastando' : '',
              alvo === i ? 'solta-antes' : '',
              alvo === clipes.length && i === clipes.length - 1 ? 'solta-depois' : '',
            ]
            return (
              <li
                key={c.arquivo}
                ref={(el) => {
                  if (el) cartoes.current.set(c.arquivo, el)
                  else cartoes.current.delete(c.arquivo)
                }}
                className={classes.filter(Boolean).join(' ')}
                tabIndex={0}
                aria-label={`Clipe ${i + 1}: ${c.nome}`}
                aria-current={aberto ? 'true' : undefined}
                draggable={!bloqueado}
                onClick={() => onSelecionar(c)}
                onKeyDown={(e) => teclado(e, c, i)}
                onDragStart={(e) => {
                  setArrastando(i)
                  e.dataTransfer.effectAllowed = 'move'
                  e.dataTransfer.setData('text/plain', String(i))
                }}
                onDragEnd={() => {
                  setArrastando(null)
                  setAlvo(null)
                }}
                onDragOver={(e) => aoArrastarSobre(e, i)}
                onDrop={soltar}
              >
                <div className="cartao-topo">
                  <span className="alca" aria-hidden="true" title={bloqueado ? DICA_BLOQUEIO : 'arraste para reordenar'}>
                    ⠿
                  </span>
                  <span className="posicao numeros">{i + 1}</span>
                  <span className="nome" title={c.nome}>
                    {c.nome}
                  </span>
                </div>
                {/* arquivo ilegível (corrompido ou movido): mostra um espaço neutro,
                    não o ícone de imagem quebrada do navegador */}
                <img
                  src={urlDoClipe(c.thumbnail_url, c)}
                  alt=""
                  className="miniatura"
                  loading="lazy"
                  onError={(e) => e.currentTarget.classList.add('sem-miniatura')}
                />
                <div className="cartao-dados">
                  <span className="numeros">
                    {fmtSeg(c.duracao)} · {c.largura ?? '?'}×{c.altura ?? '?'}
                    {c.fps ? ` · ${fmtNum(c.fps, 0)} fps` : ''}
                  </span>
                  <span className="selos">
                    {c.transcrito && <span className="selo ok" title="transcrito">T</span>}
                    {c.rosto && <span className="selo ok" title="rosto rastreado">R</span>}
                    {c.tem_audio === false && <span className="selo aviso">sem áudio</span>}
                    {c.vfr && (
                      <span
                        className="selo aviso"
                        title="fps variável: o enquadramento pode ficar alguns frames defasado; reexporte com fps fixo se notar atraso"
                      >
                        VFR
                      </span>
                    )}
                    {c.hdr && (
                      <span
                        className="selo aviso"
                        title="HDR: convertido para SDR (BT.709) com tonemapping no render"
                      >
                        HDR
                      </span>
                    )}
                    {c.trechos.length > 0 && (
                      <span className="selo" title={`mantém ${fmtSeg(c.duracao_mantida)}`}>
                        {c.trechos.length} trecho(s)
                      </span>
                    )}
                  </span>
                </div>
                <div className="cartao-botoes" title={bloqueado ? DICA_BLOQUEIO : undefined}>
                  <button
                    type="button"
                    className="icone"
                    onClick={(e) => {
                      e.stopPropagation()
                      moverPasso(i, -1)
                    }}
                    disabled={bloqueado || i === 0}
                    title={bloqueado ? DICA_BLOQUEIO : 'mover para a esquerda'}
                  >
                    ←<span className="oculto">Mover {c.nome} para a esquerda</span>
                  </button>
                  <button
                    type="button"
                    className="icone"
                    onClick={(e) => {
                      e.stopPropagation()
                      moverPasso(i, 1)
                    }}
                    disabled={bloqueado || i === clipes.length - 1}
                    title={bloqueado ? DICA_BLOQUEIO : 'mover para a direita'}
                  >
                    →<span className="oculto">Mover {c.nome} para a direita</span>
                  </button>
                  <button
                    type="button"
                    className="icone perigo"
                    onClick={(e) => {
                      e.stopPropagation()
                      onRemover(c)
                    }}
                    disabled={bloqueado}
                    title={bloqueado ? DICA_BLOQUEIO : 'remover do projeto'}
                  >
                    ✕<span className="oculto">Remover {c.nome}</span>
                  </button>
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
