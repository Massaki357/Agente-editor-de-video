interface Props {
  /** mensagem vinda do `detail` da API (ou de rede); null esconde a caixa */
  erro: string | null
  onClose?: () => void
}

/** Caixa de erro: toda falha da API aparece aqui, nunca em alert(). */
export default function ErrorBox({ erro, onClose }: Props) {
  if (!erro) return null
  return (
    <div className="erro" role="alert">
      <span>{erro}</span>
      {onClose && (
        <button type="button" className="icone" onClick={onClose} title="Fechar aviso">
          ✕<span className="oculto">Fechar aviso</span>
        </button>
      )}
    </div>
  )
}
