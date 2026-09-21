interface Props {
  erro: string | null
  onClose?: () => void
}

export default function ErrorBox({ erro, onClose }: Props) {
  if (!erro) return null
  return (
    <div className="erro" role="alert">
      <span>{erro}</span>
      {onClose && (
        <button className="link" onClick={onClose} title="fechar">
          ×
        </button>
      )}
    </div>
  )
}
