interface Props {
  /** frases vindas de `resultado.avisos` do job (vazio esconde o bloco) */
  avisos: string[]
  titulo?: string
  onFechar?: () => void
}

/** Bloco de aviso (não é erro): o que o usuário precisa saber sobre os clipes. */
export default function AvisoBox({ avisos, titulo = 'Avisos', onFechar }: Props) {
  if (avisos.length === 0) return null
  return (
    <div className="bloco-aviso" role="status">
      <div className="bloco-aviso-topo">
        <span aria-hidden="true">⚠</span>
        <strong>
          {titulo} ({avisos.length})
        </strong>
        {onFechar && (
          <button type="button" className="icone" onClick={onFechar} title="Dispensar avisos">
            ✕<span className="oculto">Dispensar avisos</span>
          </button>
        )}
      </div>
      <ul>
        {avisos.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
    </div>
  )
}
