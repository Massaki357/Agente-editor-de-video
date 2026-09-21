import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  api,
  fmtData,
  fmtSeg,
  jobAtivo,
  mensagemErro,
  type ClipOut,
  type Job,
  type JobTipo,
  type PipelineOptions,
  type ProjectOut,
} from '../api'
import Actions from './Actions'
import ClipList from './ClipList'
import ErrorBox from './ErrorBox'
import { HistoricoJobs, JobAtual } from './JobPanel'
import Result from './Result'

interface Props {
  projetoId: string
  opcoesPadrao: PipelineOptions | null
  /** avisa o App que o projeto mudou (nome, nº de clipes, vídeo), para atualizar a lista */
  onAlterado: () => void
}

const INTERVALO_POLLING = 1000

export default function ProjectView({ projetoId, opcoesPadrao, onAlterado }: Props) {
  const [projeto, setProjeto] = useState<ProjectOut | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [jobAtual, setJobAtual] = useState<Job | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [ocupado, setOcupado] = useState<string | null>(null) // texto da operação em andamento
  const [cancelando, setCancelando] = useState(false)
  const [nome, setNome] = useState('')
  const [pasta, setPasta] = useState('')
  const [arquivos, setArquivos] = useState<File[]>([])
  const inputArquivos = useRef<HTMLInputElement>(null)

  const carregar = useCallback(async () => {
    try {
      const [p, js] = await Promise.all([api.getProject(projetoId), api.listJobs(projetoId)])
      setProjeto(p)
      setNome(p.nome)
      setJobs(js)
      setJobAtual(js[0] ?? null)
    } catch (e) {
      setErro(mensagemErro(e))
    }
  }, [projetoId])

  // o App recria este componente (key) ao trocar de projeto
  useEffect(() => {
    void carregar()
  }, [carregar])

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
      const p = await fn()
      setProjeto(p)
      setNome(p.nome)
      onAlterado()
      return true
    } catch (e) {
      setErro(mensagemErro(e))
      return false
    } finally {
      setOcupado(null)
    }
  }

  if (!projeto) {
    return (
      <section className="cartao">
        {erro ? <ErrorBox erro={erro} /> : <p className="suave">carregando projeto…</p>}
      </section>
    )
  }

  const bloqueado = Boolean(projeto.job_ativo) || idAtivo !== null || ocupado !== null

  const renomear = (ev: FormEvent) => {
    ev.preventDefault()
    if (nome.trim() && nome.trim() !== projeto.nome) void alterar('renomeando…', () => api.renameProject(projeto.id, nome.trim()))
  }

  const importar = async (ev: FormEvent) => {
    ev.preventDefault()
    if (!pasta.trim()) return setErro('informe o caminho da pasta')
    if (await alterar('importando…', () => api.importFolder(projeto.id, pasta.trim()))) setPasta('')
  }

  const enviar = async (ev: FormEvent) => {
    ev.preventDefault()
    if (arquivos.length === 0) return setErro('escolha um ou mais vídeos')
    if (await alterar(`enviando ${arquivos.length} vídeo(s)…`, () => api.uploadClips(projeto.id, arquivos))) {
      setArquivos([])
      if (inputArquivos.current) inputArquivos.current.value = ''
    }
  }

  const mover = (i: number, delta: -1 | 1) => {
    const ordem = projeto.clipes.map((_, k) => k)
    ;[ordem[i], ordem[i + delta]] = [ordem[i + delta], ordem[i]]
    void alterar('reordenando…', () => api.reorderClips(projeto.id, ordem))
  }

  const remover = (c: ClipOut) => {
    if (!window.confirm(`Remover o clipe "${c.nome}" do projeto?`)) return
    void alterar('removendo…', () => api.removeClip(projeto.id, c.indice))
  }

  const rodar = async (tipo: JobTipo, opcoes?: PipelineOptions) => {
    setErro(null)
    try {
      const j = await api.createJob(projeto.id, tipo, opcoes)
      setJobAtual(j)
      setJobs((js) => [j, ...js])
      setProjeto({ ...projeto, job_ativo: j.id })
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

  return (
    <div className="projeto">
      <section className="cartao">
        <form onSubmit={renomear} className="linha">
          <input className="titulo" value={nome} onChange={(e) => setNome(e.target.value)} maxLength={120} />
          <button type="submit" disabled={!nome.trim() || nome.trim() === projeto.nome || ocupado !== null}>
            Renomear
          </button>
          <button type="button" className="link" onClick={() => void carregar()}>
            recarregar
          </button>
        </form>
        <p className="suave">
          id {projeto.id} · criado {fmtData(projeto.criado)} · {projeto.n_clipes} clipe(s) · duração total{' '}
          {fmtSeg(projeto.duracao_total)}
        </p>
        {ocupado && <p className="aviso-texto">{ocupado}</p>}
        {bloqueado && !ocupado && <p className="aviso-texto">Há um job em andamento: alterações desabilitadas.</p>}
        <ErrorBox erro={erro} onClose={() => setErro(null)} />
      </section>

      <section className="cartao">
        <h3>Clipes</h3>
        <form onSubmit={importar} className="linha">
          <input
            className="largo"
            value={pasta}
            onChange={(e) => setPasta(e.target.value)}
            placeholder="pasta local: caminho absoluto ou relativo à raiz do projeto (ex.: samples)"
          />
          <button type="submit" disabled={bloqueado}>
            Importar pasta
          </button>
        </form>
        <form onSubmit={enviar} className="linha">
          <input
            ref={inputArquivos}
            type="file"
            multiple
            accept=".mp4,.mov,.mkv"
            onChange={(e) => setArquivos(Array.from(e.target.files ?? []))}
          />
          <button type="submit" disabled={bloqueado || arquivos.length === 0}>
            Enviar vídeos
          </button>
        </form>
        <ClipList
          projetoId={projeto.id}
          clipes={projeto.clipes}
          bloqueado={bloqueado}
          onMover={mover}
          onRemover={remover}
        />
      </section>

      <Actions
        key={opcoesPadrao ? 'config' : 'sem-config'}
        padrao={opcoesPadrao}
        bloqueado={bloqueado}
        semClipes={projeto.clipes.length === 0}
        onRodar={(t, o) => void rodar(t, o)}
      />

      <section className="cartao">
        <h3>Job</h3>
        {jobAtual ? (
          <JobAtual job={jobAtual} onCancelar={() => void cancelar()} cancelando={cancelando} />
        ) : (
          <p className="suave">Nenhum job rodando.</p>
        )}
        <details>
          <summary>Histórico de jobs ({jobs.length})</summary>
          <HistoricoJobs jobs={jobs} />
        </details>
      </section>

      <Result projeto={projeto} />
    </div>
  )
}
