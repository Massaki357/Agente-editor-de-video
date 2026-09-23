import { jobAtivo, nomeJob, type Job, type ProjectSummary } from '../api'

interface Props {
  /** todos os jobs (de todos os projetos), mais novos primeiro — como a API devolve */
  jobs: Job[]
  /** nomes dos projetos, para mostrar de quem é cada job */
  projetos: ProjectSummary[]
  /** projeto aberto agora (o job dele aparece como "este projeto") */
  projetoAtual: string
  onAbrirProjeto: (id: string) => void
  onCancelar: (job: Job) => void
  /** ids de jobs com cancelamento já pedido */
  cancelando: readonly string[]
}

/** Fila da API (um worker só): jobs pendentes e rodando de todos os projetos, na ordem. */
export default function JobQueue({
  jobs,
  projetos,
  projetoAtual,
  onAbrirProjeto,
  onCancelar,
  cancelando,
}: Props) {
  // a API devolve do mais novo para o mais velho; a fila roda na ordem inversa
  const fila = jobs.filter(jobAtivo).slice().reverse()
  if (fila.length === 0) return null

  const nomeProjeto = (id: string) => projetos.find((p) => p.id === id)?.nome ?? id

  return (
    <section className="fila" aria-label="Fila de jobs">
      <h3 className="secao">
        Fila ({fila.length})
        <span className="suave">a API roda um job por vez</span>
      </h3>
      <ol className="fila-lista" aria-live="polite">
        {fila.map((j, i) => {
          const daqui = j.projeto_id === projetoAtual
          const pedido = j.cancelar || cancelando.includes(j.id)
          return (
            <li key={j.id}>
              <span className="posicao numeros" title={`posição ${i + 1} na fila`}>
                {i + 1}
              </span>
              <span className={`selo status-${j.status}`}>{j.status}</span>
              <span className="fila-tipo">{nomeJob(j.tipo)}</span>
              {daqui ? (
                <span className="suave">este projeto</span>
              ) : (
                <button
                  type="button"
                  className="link"
                  onClick={() => onAbrirProjeto(j.projeto_id)}
                  title={`Abrir o projeto "${nomeProjeto(j.projeto_id)}"`}
                >
                  {nomeProjeto(j.projeto_id)}
                </button>
              )}
              <button
                type="button"
                className="perigo pequeno"
                onClick={() => onCancelar(j)}
                disabled={pedido}
                title={`Cancelar ${nomeJob(j.tipo)} de "${nomeProjeto(j.projeto_id)}"`}
              >
                {pedido ? 'cancelando…' : 'Cancelar'}
              </button>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
