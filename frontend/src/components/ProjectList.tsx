import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, fmtData, mensagemErro, type ProjectSummary } from '../api'
import ErrorBox from './ErrorBox'

interface Props {
  selecionado: string | null
  onAbrir: (id: string | null) => void
  /** muda quando o projeto aberto foi alterado, para recarregar a lista */
  versao: number
}

export default function ProjectList({ selecionado, onAbrir, versao }: Props) {
  const [projetos, setProjetos] = useState<ProjectSummary[]>([])
  const [nome, setNome] = useState('')
  const [erro, setErro] = useState<string | null>(null)
  const [ocupado, setOcupado] = useState(false)

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

  const criar = async (ev: FormEvent) => {
    ev.preventDefault()
    setOcupado(true)
    setErro(null)
    try {
      const p = await api.createProject(nome.trim() || 'Novo projeto')
      setNome('')
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
    <section className="cartao">
      <h2>Projetos</h2>
      <form onSubmit={criar} className="linha">
        <input
          value={nome}
          onChange={(e) => setNome(e.target.value)}
          placeholder="nome do novo projeto"
          maxLength={120}
        />
        <button type="submit" disabled={ocupado}>
          Criar
        </button>
      </form>
      <ErrorBox erro={erro} onClose={() => setErro(null)} />
      {projetos.length === 0 ? (
        <p className="suave">Nenhum projeto ainda.</p>
      ) : (
        <ul className="projetos">
          {projetos.map((p) => (
            <li key={p.id} className={p.id === selecionado ? 'ativo' : ''}>
              <button className="link" onClick={() => onAbrir(p.id)}>
                {p.nome}
              </button>
              <span className="suave">
                {p.n_clipes} clipe(s) · {fmtData(p.atualizado)}
                {p.video_final_url ? ' · vídeo gerado' : ''}
              </span>
              <button className="perigo pequeno" onClick={() => excluir(p)}>
                Excluir
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
