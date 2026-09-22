import { useCallback, useEffect, useState } from 'react'
import {
  api,
  jobAtivo,
  mensagemErro,
  type ClipOut,
  type ConfigOut,
  type Job,
  type JobTipo,
  type PipelineOptions,
  type ProjectOut,
} from '../api'
import { usePlano } from '../usePlano'
import type { Aba } from './ClipDetails'
import ClipStrip from './ClipStrip'
import ErrorBox from './ErrorBox'
import JobPanel from './JobPanel'
import OptionsPanel from './OptionsPanel'
import Stage, { type ModoPalco } from './Stage'

interface Props {
  projetoId: string
  config: ConfigOut | null
  /** muda quando o App quer recarregar o projeto (renomear, botão ⟳) */
  recarga: number
  /** avisa o App que o projeto mudou (nome, nº de clipes, vídeo) */
  onAlterado: () => void
  /** espelha o projeto carregado no App (cabeçalho) */
  onProjeto: (p: ProjectOut | null) => void
}

const INTERVALO_POLLING = 1000

/** Cola tudo: carrega o projeto, faz o polling dos jobs e monta palco, faixa e opções. */
export default function Workspace({ projetoId, config, recarga, onAlterado, onProjeto }: Props) {
  const [projeto, setProjeto] = useState<ProjectOut | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [jobAtual, setJobAtual] = useState<Job | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [ocupado, setOcupado] = useState<string | null>(null) // texto da operação em andamento
  const [cancelando, setCancelando] = useState(false)
  const [versaoPlano, setVersaoPlano] = useState(0) // recarrega o plano criativo
  const [modo, setModo] = useState<ModoPalco>('resultado')
  const [selecionado, setSelecionado] = useState<string | null>(null) // arquivo do clipe aberto
  const [aba, setAba] = useState<Aba>('video')

  const plano = usePlano(projetoId, versaoPlano)

  const carregar = useCallback(async () => {
    try {
      const [p, js] = await Promise.all([api.getProject(projetoId), api.listJobs(projetoId)])
      setProjeto(p)
      setJobs(js)
      setJobAtual(js[0] ?? null)
    } catch (e) {
      setErro(mensagemErro(e))
    }
  }, [projetoId])

  // o App recria este componente (key) ao trocar de projeto; `recarga` força releitura
  useEffect(() => {
    void carregar()
  }, [carregar, recarga])

  useEffect(() => onProjeto(projeto), [projeto, onProjeto])

  // polling do job enquanto pendente/rodando; ao terminar, recarrega o projeto
  const idAtivo = jobAtual && jobAtivo(jobAtual) ? jobAtual.id : null
  useEffect(() => {
    if (!idAtivo) return
    let vivo = true
    const t = setInterval(async () => {
      try {
        const j = await api.getJob(idAtivo)
        if (!vivo) return
        setJobAtual(j)
        if (!jobAtivo(j)) {
          setCancelando(false)
          if (j.tipo === 'imagens' || j.tipo === 'gerar') setVersaoPlano((v) => v + 1)
          // leva o palco para o que acabou de ficar pronto
          if (j.status === 'concluido' && j.tipo === 'imagens') setModo('plano')
          if (j.status === 'concluido' && j.tipo === 'gerar') setModo('resultado')
          await carregar()
          onAlterado()
        }
      } catch (e) {
        if (vivo) setErro(mensagemErro(e))
      }
    }, INTERVALO_POLLING)
    return () => {
      vivo = false
      clearInterval(t)
    }
  }, [idAtivo, carregar, onAlterado])

  /** Roda uma alteração que devolve o projeto atualizado. */
  const alterar = async (rotulo: string, fn: () => Promise<ProjectOut>) => {
    setOcupado(rotulo)
    setErro(null)
    try {
      setProjeto(await fn())
      onAlterado()
    } catch (e) {
      setErro(mensagemErro(e))
    } finally {
      setOcupado(null)
    }
  }

  const remover = (c: ClipOut) => {
    if (!window.confirm(`Remover o clipe "${c.nome}" do projeto?`)) return
    if (selecionado === c.arquivo) {
      setSelecionado(null)
      setModo('resultado')
    }
    void alterar('removendo…', () => api.removeClip(projetoId, c.indice))
  }

  const rodar = async (tipo: JobTipo, opcoes?: PipelineOptions) => {
    setErro(null)
    try {
      const j = await api.createJob(projetoId, tipo, opcoes)
      setJobAtual(j)
      setJobs((js) => [j, ...js])
      setProjeto((p) => (p ? { ...p, job_ativo: j.id } : p))
    } catch (e) {
      setErro(mensagemErro(e))
    }
  }

  const cancelar = async () => {
    if (!jobAtual) return
    setCancelando(true)
    try {
      setJobAtual(await api.cancelJob(jobAtual.id))
    } catch (e) {
      setErro(mensagemErro(e))
      setCancelando(false)
    }
  }

  if (!projeto) {
    return (
      <section className="palco" aria-label="Palco">
        <div className="vazio" aria-live="polite">
          {erro ? <ErrorBox erro={erro} /> : <p className="suave">carregando projeto…</p>}
        </div>
      </section>
    )
  }

  const bloqueado = Boolean(projeto.job_ativo) || idAtivo !== null || ocupado !== null
  const clipe = projeto.clipes.find((c) => c.arquivo === selecionado) ?? null
  const jobGerar = jobs.find((j) => j.tipo === 'gerar' && j.status === 'concluido') ?? null

  return (
    <>
      <Stage
        projeto={projeto}
        modo={modo}
        onModo={setModo}
        clipe={clipe}
        aba={aba}
        onAba={setAba}
        jobGerar={jobGerar}
        plano={plano}
        bloqueado={bloqueado}
      />

      <ClipStrip
        clipes={projeto.clipes}
        bloqueado={bloqueado}
        ocupado={ocupado}
        selecionado={selecionado}
        onSelecionar={(c) => {
          setSelecionado(c.arquivo)
          setModo('clipe')
        }}
        onRemover={remover}
        onReordenar={(ordem) => void alterar('reordenando…', () => api.reorderClips(projetoId, ordem))}
        onImportar={(pasta) => void alterar('importando…', () => api.importFolder(projetoId, pasta))}
        onEnviar={(arquivos) =>
          void alterar(`enviando ${arquivos.length} vídeo(s)…`, () => api.uploadClips(projetoId, arquivos))
        }
      />

      <aside className="lateral" aria-label="Opções e job">
        <div className="lateral-rolagem">
          <OptionsPanel
            key={config ? 'config' : 'sem-config'}
            config={config}
            bloqueado={bloqueado}
            semClipes={projeto.clipes.length === 0}
            onRodar={(t, o) => void rodar(t, o)}
          />
        </div>
        <div className="lateral-rodape">
          <div aria-live="polite">
            <ErrorBox erro={erro} onClose={() => setErro(null)} />
            {/* sem plano carregado não há aba do palco: o erro dele aparece aqui */}
            {plano.dados === null && <ErrorBox erro={plano.erro} />}
          </div>
          <JobPanel job={jobAtual} jobs={jobs} onCancelar={() => void cancelar()} cancelando={cancelando} />
        </div>
      </aside>
    </>
  )
}
