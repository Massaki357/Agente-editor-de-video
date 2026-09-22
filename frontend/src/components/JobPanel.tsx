import { useEffect, useState } from 'react'
import { fmtData, fmtRelogio, jobAtivo, nomeJob, type Job } from '../api'

interface Props {
  /** job mais recente do projeto (null quando nunca rodou nada) */
  job: Job | null
  jobs: Job[]
  onCancelar: () => void
  cancelando: boolean
}

/** Rodapé da coluna de opções: job atual, progresso, tempo, cancelar e histórico. */
export default function JobPanel({ job, jobs, onCancelar, cancelando }: Props) {
  const ativo = job !== null && jobAtivo(job)
  const decorrido = useDecorrido(job, ativo)

  return (
    <div className={`job${job ? ` status-${job.status}` : ''}`}>
      {job ? (
        <>
          <div className="job-topo" aria-live="polite">
            <strong>{nomeJob(job.tipo)}</strong>
            {job.etapa && <span className="suave">· {job.etapa}</span>}
            <span className={`selo status-${job.status}`}>{job.status}</span>
          </div>

          <div className="barra" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(job.progresso * 100)}>
            <div style={{ transform: `scaleX(${job.progresso})` }} />
          </div>

          <div className="job-rodape">
            <span className="suave numeros">
            {Math.round(job.progresso * 100)}% · {fmtRelogio(decorrido)}
          </span>
            <span aria-live="polite" className="suave">
              {job.mensagem ?? (ativo ? 'em andamento…' : '')}
            </span>
            {ativo && (
              <button type="button" className="perigo pequeno" onClick={onCancelar} disabled={cancelando || job.cancelar}>
                {job.cancelar ? 'cancelando…' : 'Cancelar'}
              </button>
            )}
          </div>

          {job.status === 'erro' && job.mensagem && <p className="texto-erro">{job.mensagem}</p>}

          {job.log.length > 0 && (
            <details open={ativo || job.status === 'erro'}>
              <summary>log ({job.log.length} linhas, últimas 15)</summary>
              <pre className="log">{job.log.slice(-15).join('\n')}</pre>
            </details>
          )}
          {job.resultado && !ativo && (
            <details>
              <summary>resultado</summary>
              <pre>{JSON.stringify(job.resultado, null, 2)}</pre>
            </details>
          )}
        </>
      ) : (
        <p className="suave">Nenhum job rodando.</p>
      )}

      <details>
        <summary>Histórico ({jobs.length})</summary>
        <Historico jobs={jobs} />
      </details>
    </div>
  )
}

/** Segundos desde o início do job (congela quando ele termina). */
function useDecorrido(job: Job | null, ativo: boolean): number | null {
  const [agora, setAgora] = useState(() => Date.now())
  useEffect(() => {
    if (!ativo) return
    const t = setInterval(() => setAgora(Date.now()), 1000)
    return () => clearInterval(t)
  }, [ativo, job?.id])
  if (!job) return null
  const inicio = new Date(job.iniciado ?? job.criado).getTime()
  const fim = job.terminado ? new Date(job.terminado).getTime() : agora
  return (fim - inicio) / 1000
}

function Historico({ jobs }: { jobs: Job[] }) {
  if (jobs.length === 0) return <p className="suave">Nenhum job ainda.</p>
  return (
    <ul className="historico">
      {jobs.map((j) => (
        <li key={j.id}>
          <span className={`selo status-${j.status}`}>{j.status}</span>
          <span>{nomeJob(j.tipo)}</span>
          <span className="suave numeros">{fmtData(j.terminado ?? j.criado)}</span>
          {j.mensagem && <span className="suave">{j.mensagem}</span>}
        </li>
      ))}
    </ul>
  )
}
