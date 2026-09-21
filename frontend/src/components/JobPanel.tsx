import { fmtData, jobAtivo, nomeJob, type Job } from '../api'

interface AtualProps {
  job: Job
  onCancelar: () => void
  cancelando: boolean
}

/** O job mais recente do projeto: status, etapa, progresso, log e cancelar. */
export function JobAtual({ job, onCancelar, cancelando }: AtualProps) {
  const ativo = jobAtivo(job)
  const pct = Math.round(job.progresso * 100)
  return (
    <div className={`job status-${job.status}`}>
      <div className="linha">
        <strong>{nomeJob(job.tipo)}</strong>
        <span className={`selo status-${job.status}`}>{job.status}</span>
        {job.etapa && <span>etapa: {job.etapa}</span>}
        <span className="suave">#{job.id}</span>
        {ativo && (
          <button className="perigo pequeno" onClick={onCancelar} disabled={cancelando || job.cancelar}>
            {job.cancelar ? 'cancelando…' : 'Cancelar'}
          </button>
        )}
      </div>
      <div className="barra" aria-label="progresso">
        <div style={{ width: `${pct}%` }} />
        <span>{pct}%</span>
      </div>
      {job.mensagem && <p className={job.status === 'erro' ? 'erro-texto' : ''}>{job.mensagem}</p>}
      {job.resultado && !ativo && (
        <details>
          <summary>resultado</summary>
          <pre>{JSON.stringify(job.resultado, null, 2)}</pre>
        </details>
      )}
      {job.log.length > 0 && (
        <details open={ativo || job.status === 'erro'}>
          <summary>log ({job.log.length} linhas, últimas 15)</summary>
          <pre className="log">{job.log.slice(-15).join('\n')}</pre>
        </details>
      )}
    </div>
  )
}

export function HistoricoJobs({ jobs }: { jobs: Job[] }) {
  if (jobs.length === 0) return <p className="suave">Nenhum job ainda.</p>
  return (
    <table>
      <thead>
        <tr>
          <th>tipo</th>
          <th>status</th>
          <th>criado</th>
          <th>terminado</th>
          <th>mensagem</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((j) => (
          <tr key={j.id}>
            <td>{nomeJob(j.tipo)}</td>
            <td>
              <span className={`selo status-${j.status}`}>{j.status}</span>
            </td>
            <td>{fmtData(j.criado)}</td>
            <td>{fmtData(j.terminado)}</td>
            <td className="suave">{j.mensagem ?? ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
