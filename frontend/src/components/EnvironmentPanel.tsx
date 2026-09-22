import { useCallback, useEffect, useRef, useState } from 'react'
import { api, mensagemErro, type CheckOut, type ConfigOut } from '../api'
import ErrorBox from './ErrorBox'

interface Props {
  config: ConfigOut | null
  onFechar: () => void
}

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

  // fecha com Esc
  useEffect(() => {
    const aoTeclar = (e: KeyboardEvent) => e.key === 'Escape' && onFechar()
    window.addEventListener('keydown', aoTeclar)
    return () => window.removeEventListener('keydown', aoTeclar)
  }, [onFechar])

  // o foco entra na gaveta ao abrir e volta para quem a abriu ao fechar
  const fechar = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const anterior = document.activeElement as HTMLElement | null
    fechar.current?.focus()
    return () => anterior?.focus?.()
  }, [])

  return (
    <div className="sobreposicao" onClick={onFechar}>
      <aside
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
