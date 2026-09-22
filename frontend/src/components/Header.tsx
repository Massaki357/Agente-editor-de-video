import { useEffect, useRef, useState, type SyntheticEvent } from 'react'
import { api, fmtSeg, type ConfigOut, type ProjectOut } from '../api'

interface Props {
  config: ConfigOut | null
  /** projeto aberto (null quando nenhum): dá o nome editável do cabeçalho */
  projeto: ProjectOut | null
  onRenomear: (nome: string) => void
  onRecarregar: () => void
  onAmbiente: () => void
}

/** Cabeçalho fixo: marca, nome do projeto, estado da API e acesso ao Ambiente. */
export default function Header({ config, projeto, onRenomear, onRecarregar, onAmbiente }: Props) {
  const [online, setOnline] = useState<boolean | null>(null)
  const [editando, setEditando] = useState(false)
  const [nome, setNome] = useState('')
  const campo = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let vivo = true
    const checar = () =>
      api
        .health()
        .then(() => vivo && setOnline(true))
        .catch(() => vivo && setOnline(false))
    void checar()
    const t = setInterval(checar, 10000)
    return () => {
      vivo = false
      clearInterval(t)
    }
  }, [])

  // foca o campo ao entrar no modo de edição
  useEffect(() => {
    if (editando) campo.current?.select()
  }, [editando])

  const abrirEdicao = () => {
    setNome(projeto?.nome ?? '')
    setEditando(true)
  }

  const salvar = (ev: SyntheticEvent) => {
    ev.preventDefault()
    const limpo = nome.trim()
    if (limpo && limpo !== projeto?.nome) onRenomear(limpo)
    setEditando(false)
  }

  const bloqueado = Boolean(projeto?.job_ativo)
  const estado = online === null ? 'verificando…' : online ? 'API no ar' : 'API fora do ar'

  return (
    <header className="topo">
      <div className="marca">
        <span aria-hidden="true">▶</span> EDITOR
      </div>

      {projeto &&
        (editando ? (
          <form className="nome-projeto" onSubmit={salvar}>
            <label className="oculto" htmlFor="nome-projeto">
              Nome do projeto
            </label>
            <input
              id="nome-projeto"
              ref={campo}
              value={nome}
              maxLength={120}
              onChange={(e) => setNome(e.target.value)}
              onBlur={salvar}
              onKeyDown={(e) => e.key === 'Escape' && setEditando(false)}
            />
            <button type="submit" className="icone" title="Salvar nome">
              ✓
            </button>
          </form>
        ) : (
          <div className="nome-projeto">
            <h1>{projeto.nome}</h1>
            <button
              type="button"
              className="icone"
              onClick={abrirEdicao}
              disabled={bloqueado}
              title={bloqueado ? 'há um job em andamento' : 'Renomear projeto'}
            >
              ✎<span className="oculto">Renomear projeto</span>
            </button>
            <button type="button" className="icone" onClick={onRecarregar} title="Recarregar projeto">
              ⟳<span className="oculto">Recarregar projeto</span>
            </button>
          </div>
        ))}

      {projeto && projeto.clipes.length > 0 && (
        <span className="suave numeros" title="duração dos clipes → duração depois dos cortes">
          {fmtSeg(projeto.clipes.reduce((s, c) => s + (c.duracao ?? 0), 0))}
          {projeto.duracao_total > 0 ? ` → ${fmtSeg(projeto.duracao_total)}` : ''}
        </span>
      )}

      <div className="topo-direita">
        <span
          className={`estado ${online ? 'ok' : online === false ? 'falta' : ''}`}
          aria-live="polite"
        >
          <span className="ponto" aria-hidden="true" /> {estado}
          {config ? ` · Whisper ${config.whisper_device}` : ''}
        </span>
        <button type="button" onClick={onAmbiente}>
          Ambiente ⚙
        </button>
      </div>
    </header>
  )
}
