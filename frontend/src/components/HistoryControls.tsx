import { fmtData, type HistoryOut } from '../api'

interface Props {
  history: HistoryOut | null
  erro: string | null
  bloqueado: boolean
  onDesfazer: () => void
  onRefazer: () => void
}

/** Versões do projeto; os comandos iniciam um job e atualizam o vídeo ao terminar. */
export default function HistoryControls({ history, erro, bloqueado, onDesfazer, onRefazer }: Props) {
  const entries = history?.entries ?? []
  const cursor = history?.cursor ?? -1
  const atual = entries[cursor]

  return (
    <section className="historico-edicao" aria-label="Histórico de edições">
      <div className="historico-edicao-topo">
        <div>
          <strong>Versões do vídeo</strong>
          {history && entries.length > 0 && (
            <span className="suave numeros"> · {cursor + 1} de {entries.length}</span>
          )}
        </div>
        <div className="historico-edicao-acoes">
          <button type="button" className="pequeno" disabled={bloqueado || !history?.can_undo} onClick={onDesfazer}>
            Desfazer
          </button>
          <button type="button" className="pequeno" disabled={bloqueado || !history?.can_redo} onClick={onRefazer}>
            Refazer
          </button>
        </div>
      </div>
      {erro ? (
        <p className="texto-erro" role="alert">Não foi possível carregar as versões: {erro}</p>
      ) : history ? (
        <>
          <p className="suave" aria-live="polite">{atual?.summary ?? 'Nenhuma edição registrada.'}</p>
          {entries.length > 1 && (
            <details>
              <summary>Ver versões</summary>
              <ol className="historico-edicao-lista">
                {entries.map((entry, index) => (
                  <li key={entry.version} aria-current={index === cursor ? 'step' : undefined}>
                    <span className="numeros">v{entry.version}</span>
                    <span>{entry.summary}</span>
                    <span className="suave numeros">{fmtData(entry.created_at)}</span>
                  </li>
                ))}
              </ol>
            </details>
          )}
        </>
      ) : (
        <p className="suave">Carregando versões…</p>
      )}
    </section>
  )
}
