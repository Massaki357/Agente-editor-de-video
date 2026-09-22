import { useCallback, useEffect, useState } from 'react'
import { api, mensagemErro, type ConfigOut, type ProjectOut } from './api'
import EnvironmentPanel from './components/EnvironmentPanel'
import ErrorBox from './components/ErrorBox'
import Header from './components/Header'
import ProjectBar from './components/ProjectBar'
import Workspace from './components/Workspace'

// o projeto aberto fica no hash (#<id>) para sobreviver a um recarregamento da página
const lerHash = () => decodeURIComponent(window.location.hash.slice(1)) || null

export default function App() {
  const [config, setConfig] = useState<ConfigOut | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [aberto, setAberto] = useState<string | null>(lerHash)
  const [versao, setVersao] = useState(0) // muda para a barra de projetos recarregar
  const [recarga, setRecarga] = useState(0) // muda para o workspace recarregar o projeto
  const [projeto, setProjeto] = useState<ProjectOut | null>(null) // espelho do projeto aberto
  const [ambiente, setAmbiente] = useState(false)

  useEffect(() => {
    api
      .config()
      .then(setConfig)
      .catch((e) => setErro(`não foi possível ler a configuração: ${mensagemErro(e)}`))
    const aoMudar = () => setAberto(lerHash())
    window.addEventListener('hashchange', aoMudar)
    return () => window.removeEventListener('hashchange', aoMudar)
  }, [])

  const abrir = useCallback((id: string | null) => {
    setAberto(id)
    setProjeto(null)
    history.replaceState(null, '', id ? `#${encodeURIComponent(id)}` : window.location.pathname)
  }, [])

  const alterado = useCallback(() => setVersao((v) => v + 1), [])
  const recarregar = useCallback(() => setRecarga((r) => r + 1), [])

  const renomear = useCallback(
    async (nome: string) => {
      if (!aberto) return
      setErro(null)
      try {
        setProjeto(await api.renameProject(aberto, nome))
        setVersao((v) => v + 1)
      } catch (e) {
        setErro(mensagemErro(e))
      }
    },
    [aberto],
  )

  return (
    <div className="app">
      <Header
        config={config}
        projeto={projeto}
        onRenomear={renomear}
        onRecarregar={recarregar}
        onAmbiente={() => setAmbiente(true)}
      />

      {erro && (
        <div className="faixa-erro" aria-live="polite">
          <ErrorBox erro={erro} onClose={() => setErro(null)} />
        </div>
      )}

      {aberto ? (
        <Workspace
          key={aberto}
          projetoId={aberto}
          config={config}
          recarga={recarga}
          onAlterado={alterado}
          onProjeto={setProjeto}
        />
      ) : (
        <section className="palco" aria-label="Palco">
          <div className="vazio">
            <h2>Nenhum projeto aberto</h2>
            <p className="suave">
              Crie um projeto na barra de baixo, importe seus vídeos e clique em Gerar vídeo.
            </p>
          </div>
        </section>
      )}

      <ProjectBar selecionado={aberto} onAbrir={abrir} versao={versao} />

      {ambiente && <EnvironmentPanel config={config} onFechar={() => setAmbiente(false)} />}
    </div>
  )
}
