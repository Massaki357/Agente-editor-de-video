import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { api, fmtData, mensagemErro, type ProjectSummary } from '../api'
import ErrorBox from './ErrorBox'

interface Props {
  selecionado: string | null
  onAbrir: (id: string | null) => void
  /** muda quando algum projeto foi alterado, para recarregar a lista */
  versao: number
}

/** Barra da base: troca de projeto, criação e exclusão. */
export default function ProjectBar({ selecionado, onAbrir, versao }: Props) {
  const [projetos, setProjetos] = useState<ProjectSummary[]>([])
  const [criando, setCriando] = useState(false)
  const [nome, setNome] = useState('')
  const [erro, setErro] = useState<string | null>(null)
  const [ocupado, setOcupado] = useState(false)
  const campo = useRef<HTMLInputElement>(null)

  const carregar = useCallback(async () => {
    try {
      setProjetos(await api.listProjects())
    } catch (e) {
      setErro(mensagemErro(e))
    }
  }, [])

  useEffect(() => {
    void carregar()
  }, [carregar, versao])

  useEffect(() => {
    if (criando) campo.current?.focus()
  }, [criando])

  const criar = async (ev: FormEvent) => {
    ev.preventDefault()
    setOcupado(true)
    setErro(null)
    try {
      const p = await api.createProject(nome.trim() || 'Novo projeto')
      setNome('')
      setCriando(false)
      await carregar()
      onAbrir(p.id)
    } catch (e) {
      setErro(mensagemErro(e))
    } finally {
      setOcupado(false)
    }
  }

  const excluir = async (p: ProjectSummary) => {
    if (!window.confirm(`Excluir o projeto "${p.nome}"? Os arquivos enviados e gerados serão apagados.`))
      return
    setErro(null)
    try {
      await api.deleteProject(p.id)
      if (selecionado === p.id) onAbrir(null)
      await carregar()
    } catch (e) {
      setErro(mensagemErro(e))
    }
  }

  return (
    <footer className="projetos" aria-label="Projetos">
      <span className="secao">Projetos</span>
      <div className="trilha-projetos">
        {projetos.length === 0 && <span className="suave">Nenhum projeto ainda.</span>}
        {projetos.map((p) => {
          const ativo = p.id === selecionado
          return (
            <span key={p.id} className={`chip-projeto${ativo ? ' ativo' : ''}`}>
              <button
                type="button"
                className="chip-abrir"
                onClick={() => onAbrir(p.id)}
                aria-current={ativo ? 'true' : undefined}
                title={`${p.n_clipes} clipe(s) · atualizado ${fmtData(p.atualizado)}${
                  p.video_final_url ? ' · vídeo gerado' : ''
                }`}
              >
                {ativo ? '▸ ' : ''}
                {p.nome}
                <span className="suave"> · {p.n_clipes}</span>
              </button>
              <button
                type="button"
                className="icone perigo"
                onClick={() => void excluir(p)}
                title={`Excluir ${p.nome}`}
              >
                ✕<span className="oculto">Excluir {p.nome}</span>
              </button>
            </span>
          )
        })}

        {criando ? (
          <form className="novo-projeto" onSubmit={criar}>
            <label className="oculto" htmlFor="novo-projeto">
              Nome do novo projeto
            </label>
            <input
              id="novo-projeto"
              ref={campo}
              value={nome}
              maxLength={120}
              placeholder="nome do projeto"
              onChange={(e) => setNome(e.target.value)}
              onKeyDown={(e) => e.key === 'Escape' && setCriando(false)}
            />
            <button type="submit" disabled={ocupado}>
              Criar
            </button>
            <button type="button" className="link" onClick={() => setCriando(false)}>
              cancelar
            </button>
          </form>
        ) : (
          <button type="button" className="chip-novo" onClick={() => setCriando(true)}>
            + novo
          </button>
        )}
      </div>
      <div aria-live="polite">
        <ErrorBox erro={erro} onClose={() => setErro(null)} />
      </div>
    </footer>
  )
}
