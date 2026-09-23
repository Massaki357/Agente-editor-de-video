import { useCallback, useEffect, useRef, useState } from 'react'
import { api, mensagemErro, type CheckOut, type ConfigOut } from '../api'
import ErrorBox from './ErrorBox'

interface Props {
  config: ConfigOut | null
  onFechar: () => void
}

/** O que recebe foco pelo Tab dentro da gaveta (usado para prender o foco nela). */
const FOCAVEIS =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])'

/** Painel lateral com a configuração da API e as checagens do doctor. */
export default function EnvironmentPanel({ config, onFechar }: Props) {
  const [checks, setChecks] = useState<CheckOut[] | null>(null)
  const [carregando, setCarregando] = useState(false)
  const [erro, setErro] = useState<string | null>(null)

  const rodarDoctor = useCallback(async () => {
    setCarregando(true)
    setErro(null)
    try {
      setChecks(await api.doctor())
    } catch (e) {
      setErro(mensagemErro(e))
    } finally {
      setCarregando(false)
    }
  }, [])

  useEffect(() => {
    void rodarDoctor()
  }, [rodarDoctor])

  // o foco entra na gaveta ao abrir e volta para quem a abriu ao fechar
  const fechar = useRef<HTMLButtonElement>(null)
  const gaveta = useRef<HTMLElement>(null)
  useEffect(() => {
    const anterior = document.activeElement as HTMLElement | null
    fechar.current?.focus()
    return () => anterior?.focus?.()
  }, [])

  // fecha com Esc; enquanto aberta, o Tab circula dentro da gaveta (diálogo modal)
  useEffect(() => {
    const aoTeclar = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onFechar()
        return
      }
      if (e.key !== 'Tab' || !gaveta.current) return
      // getClientRects(): descarta o que não está na tela (display:none, <details> fechado)
      const alvos = Array.from(gaveta.current.querySelectorAll<HTMLElement>(FOCAVEIS)).filter(
        (el) => el.getClientRects().length > 0 || el === document.activeElement,
      )
      if (alvos.length === 0) return
      const atual = alvos.indexOf(document.activeElement as HTMLElement)
      const primeiro = 0
      const ultimo = alvos.length - 1
      if (atual < 0) {
        e.preventDefault()
        alvos[e.shiftKey ? ultimo : primeiro].focus()
      } else if (e.shiftKey && atual === primeiro) {
        e.preventDefault()
        alvos[ultimo].focus()
      } else if (!e.shiftKey && atual === ultimo) {
        e.preventDefault()
        alvos[primeiro].focus()
      }
    }
    window.addEventListener('keydown', aoTeclar)
    return () => window.removeEventListener('keydown', aoTeclar)
  }, [onFechar])

  return (
    <div className="sobreposicao" onClick={onFechar}>
      <aside
        ref={gaveta}
        className="gaveta"
        role="dialog"
        aria-modal="true"
        aria-label="Ambiente"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="gaveta-topo">
          <h2>Ambiente</h2>
          <button type="button" className="icone" ref={fechar} onClick={onFechar} title="Fechar">
            ✕<span className="oculto">Fechar</span>
          </button>
        </div>

        <div className="gaveta-corpo">
          <h3 className="secao">Configuração</h3>
          {config ? (
            <dl className="dados">
              <dt>Modelo do LLM (.env)</dt>
              <dd>{config.llm_model}</dd>
              <dt>Modelos disponíveis</dt>
              <dd>{config.llm_models.join(', ') || '—'}</dd>
              <dt>Whisper</dt>
              <dd>
                {config.whisper_model} ({config.whisper_device})
              </dd>
              <dt>Saída</dt>
              <dd>
                {config.saida.largura}×{config.saida.altura} @ {config.saida.fps} fps
              </dd>
            </dl>
          ) : (
            <p className="suave">Configuração indisponível.</p>
          )}

          <h3 className="secao">
            Doctor
            <button type="button" className="pequeno" onClick={() => void rodarDoctor()} disabled={carregando}>
              {carregando ? 'verificando…' : 'verificar de novo'}
            </button>
          </h3>
          <div aria-live="polite">
            <ErrorBox erro={erro} />
          </div>
          {checks && (
            <ul className="checks">
              {checks.map((c) => (
                <li key={c.nome}>
                  <span className={`selo ${c.status.toLowerCase()}`}>{c.status}</span>
                  <strong>{c.nome}</strong>
                  <span className="suave">{c.detalhe}</span>
                </li>
              ))}
            </ul>
          )}

          <p>
            <a href="/docs" target="_blank" rel="noreferrer">
              Documentação da API (/docs)
            </a>
          </p>
        </div>
      </aside>
    </div>
  )
}
