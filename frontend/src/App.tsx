import { useCallback, useEffect, useState } from 'react'
import { api, mensagemErro, type ConfigOut } from './api'
import ErrorBox from './components/ErrorBox'
import Header from './components/Header'
import ProjectList from './components/ProjectList'
import ProjectView from './components/ProjectView'

// o projeto aberto fica no hash (#<id>) para sobreviver a um recarregamento da página
const lerHash = () => decodeURIComponent(window.location.hash.slice(1)) || null

export default function App() {
  const [config, setConfig] = useState<ConfigOut | null>(null)
  const [erroConfig, setErroConfig] = useState<string | null>(null)
  const [aberto, setAberto] = useState<string | null>(lerHash)
  const [versao, setVersao] = useState(0)

  useEffect(() => {
    api
      .config()
      .then(setConfig)
      .catch((e) => setErroConfig(`não foi possível ler a configuração: ${mensagemErro(e)}`))
    const aoMudar = () => setAberto(lerHash())
    window.addEventListener('hashchange', aoMudar)
    return () => window.removeEventListener('hashchange', aoMudar)
  }, [])

  const abrir = useCallback((id: string | null) => {
    setAberto(id)
    history.replaceState(null, '', id ? `#${encodeURIComponent(id)}` : window.location.pathname)
  }, [])

  const alterado = useCallback(() => setVersao((v) => v + 1), [])

  return (
    <>
      <Header config={config} />
      <ErrorBox erro={erroConfig} onClose={() => setErroConfig(null)} />
      <main className="layout">
        <aside>
          <ProjectList selecionado={aberto} onAbrir={abrir} versao={versao} />
        </aside>
        <div>
          {aberto ? (
            <ProjectView
              key={aberto}
              projetoId={aberto}
              opcoesPadrao={config?.opcoes_padrao ?? null}
              onAlterado={alterado}
            />
          ) : (
            <section className="cartao suave">Crie ou abra um projeto.</section>
          )}
        </div>
      </main>
    </>
  )
}
