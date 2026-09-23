import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  avisosDoResultado,
  jobAtivo,
  mensagemErro,
  type ClipOut,
  type ConfigOut,
  type Job,
  type JobTipo,
  type PipelineOptions,
  type ProjectOut,
  type ProjectSummary,
} from '../api'
import { usePlano } from '../usePlano'
import AvisoBox from './AvisoBox'
import type { Aba } from './ClipDetails'
import ClipStrip from './ClipStrip'
import ErrorBox from './ErrorBox'
import JobPanel from './JobPanel'
import JobQueue from './JobQueue'
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
  /** abre outro projeto (clique no nome na fila de jobs) */
  onAbrirProjeto: (id: string) => void
}

const INTERVALO_POLLING = 1000

/** Cola tudo: carrega o projeto, faz o polling dos jobs e monta palco, faixa e opções. */
export default function Workspace({
  projetoId,
  config,
  recarga,
  onAlterado,
  onProjeto,
  onAbrirProjeto,
}: Props) {
  const [projeto, setProjeto] = useState<ProjectOut | null>(null)
  // jobs de todos os projetos (mais novos primeiro): a fila da API é global
  const [todosJobs, setTodosJobs] = useState<Job[]>([])
  const [projetos, setProjetos] = useState<ProjectSummary[]>([])
  const [erro, setErro] = useState<string | null>(null)
  const [ocupado, setOcupado] = useState<string | null>(null) // texto da operação em andamento
  const [cancelando, setCancelando] = useState<string[]>([]) // jobs com cancelamento pedido
  const [versaoPlano, setVersaoPlano] = useState(0) // recarrega o plano criativo
  const [modo, setModo] = useState<ModoPalco>('resultado')
  const [selecionado, setSelecionado] = useState<string | null>(null) // arquivo do clipe aberto
  const [aba, setAba] = useState<Aba>('video')
  const [avisosOcultos, setAvisosOcultos] = useState<string | null>(null) // job com avisos dispensados
  const tratados = useRef(new Set<string>()) // jobs cujo fim já recarregou o projeto

  const plano = usePlano(projetoId, versaoPlano)

  const carregar = useCallback(async () => {
    try {
      const [p, js, ps] = await Promise.all([
        api.getProject(projetoId),
        api.listJobs(),
        api.listProjects(),
      ])
      setProjeto(p)
      setTodosJobs(js)
      setProjetos(ps)
    } catch (e) {
      setErro(mensagemErro(e))
    }
  }, [projetoId])

  // o App recria este componente (key) ao trocar de projeto; `recarga` força releitura
  useEffect(() => {
    void carregar()
  }, [carregar, recarga])

  useEffect(() => onProjeto(projeto), [projeto, onProjeto])

  const jobs = todosJobs.filter((j) => j.projeto_id === projetoId)
  const jobAtual = jobs[0] ?? null
  const idAtivo = jobAtual && jobAtivo(jobAtual) ? jobAtual.id : null
  // um timer só: enquanto a fila tiver algo, relê todos os jobs (inclui o deste projeto)
  const filaViva = todosJobs.some(jobAtivo)

  useEffect(() => {
    if (!filaViva) return
    let vivo = true
    const t = setInterval(async () => {
      try {
        const js = await api.listJobs()
        if (!vivo) return
        setTodosJobs(js)
        const j = idAtivo ? js.find((x) => x.id === idAtivo) : undefined
        if (j && !jobAtivo(j) && !tratados.current.has(j.id)) {
          tratados.current.add(j.id)
          setCancelando((c) => c.filter((id) => id !== j.id))
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
  }, [filaViva, idAtivo, carregar, onAlterado])

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
      setTodosJobs((js) => [j, ...js])
      setProjeto((p) => (p ? { ...p, job_ativo: j.id } : p))
    } catch (e) {
      setErro(mensagemErro(e))
    }
  }

  const cancelar = async (job: Job) => {
    setCancelando((c) => (c.includes(job.id) ? c : [...c, job.id]))
    try {
      const j = await api.cancelJob(job.id)
      setTodosJobs((js) => js.map((x) => (x.id === j.id ? j : x)))
      // job pendente morre na hora: sem isso o projeto ficaria travado até recarregar
      if (!jobAtivo(j) && j.projeto_id === projetoId && !tratados.current.has(j.id)) {
        tratados.current.add(j.id)
        await carregar()
        onAlterado()
      }
    } catch (e) {
      setErro(mensagemErro(e))
      setCancelando((c) => c.filter((id) => id !== job.id))
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
  // avisos do último job do projeto, até o usuário dispensá-los
  const avisos = jobAtual && avisosOcultos !== jobAtual.id ? avisosDoResultado(jobAtual.resultado) : []

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
          <AvisoBox avisos={avisos} onFechar={() => jobAtual && setAvisosOcultos(jobAtual.id)} />
          <JobPanel
            job={jobAtual}
            jobs={jobs}
            onCancelar={() => {
              if (jobAtual) void cancelar(jobAtual)
            }}
            cancelando={jobAtual !== null && cancelando.includes(jobAtual.id)}
          />
          <JobQueue
            jobs={todosJobs}
            projetos={projetos}
            projetoAtual={projetoId}
            onAbrirProjeto={onAbrirProjeto}
            onCancelar={(j) => void cancelar(j)}
            cancelando={cancelando}
          />
        </div>
      </aside>
    </>
  )
}
