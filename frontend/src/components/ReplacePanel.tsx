import { useEffect, useState, type FormEvent } from 'react'
import { mensagemErro, type Substituicao } from '../api'

export interface AlternativaMidia {
  indice: number
  rotulo: string
  miniatura?: string
  pagina?: string
}

interface Props {
  elementoId: string
  tipo: 'imagem' | 'vídeo'
  queryAtual: string
  alternativas: AlternativaMidia[]
  bloqueado: boolean
  onSubstituir: (elementoId: string, troca: Substituicao) => Promise<void>
}

/** Troca de mídia no vídeo já gerado. Cada confirmação inicia um job de render incremental. */
export default function ReplacePanel({ elementoId, tipo, queryAtual, alternativas, bloqueado, onSubstituir }: Props) {
  const [aberto, setAberto] = useState(false)
  const [modo, setModo] = useState<Substituicao['modo']>(alternativas.length ? 'alternativa' : 'busca')
  const [query, setQuery] = useState(queryAtual)
  const [indice, setIndice] = useState(alternativas[0]?.indice ?? 0)
  const [arquivo, setArquivo] = useState<File | null>(null)
  const [enviando, setEnviando] = useState(false)
  const [erro, setErro] = useState<string | null>(null)
  useEffect(() => setQuery(queryAtual), [queryAtual])
  const campoId = `troca-${elementoId}`
  const indisponivel = bloqueado || enviando
  const buscaValida = query.trim().length >= 2 && query.trim() !== queryAtual
  const alternativaValida = alternativas.some((a) => a.indice === indice)
  const pronto = modo === 'busca' ? buscaValida : modo === 'alternativa' ? alternativaValida : arquivo !== null

  async function confirmar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (indisponivel || !pronto) return
    const troca: Substituicao = modo === 'busca'
      ? { modo, query: query.trim() }
      : modo === 'alternativa'
        ? { modo, indice }
        : { modo, arquivo: arquivo! }
    setEnviando(true)
    setErro(null)
    try {
      await onSubstituir(elementoId, troca)
      setAberto(false)
      setArquivo(null)
    } catch (e) {
      setErro(mensagemErro(e))
    } finally {
      setEnviando(false)
    }
  }

  return (
    <div className="troca-midia">
      <button type="button" className="pequeno" disabled={indisponivel} aria-expanded={aberto} aria-controls={campoId} onClick={() => { setAberto(!aberto); setErro(null) }}>
        {aberto ? 'fechar troca' : tipo === 'vídeo' ? 'substituir B-roll' : 'substituir imagem no vídeo'}
      </button>
      {aberto && (
        <form id={campoId} onSubmit={(e) => void confirmar(e)}>
          <p className="suave">Escolha a origem. A confirmação atualiza o vídeo final.</p>
          <div className="linha" role="group" aria-label={`Origem do novo ${tipo}`}>
            <label><input type="radio" name={`${campoId}-modo`} checked={modo === 'alternativa'} disabled={indisponivel || alternativas.length === 0} onChange={() => setModo('alternativa')} /> alternativa</label>
            <label><input type="radio" name={`${campoId}-modo`} checked={modo === 'busca'} disabled={indisponivel} onChange={() => setModo('busca')} /> nova busca</label>
            <label><input type="radio" name={`${campoId}-modo`} checked={modo === 'upload'} disabled={indisponivel} onChange={() => setModo('upload')} /> meu arquivo</label>
          </div>
          {modo === 'alternativa' && (
            alternativas.length ? (
              <div className="troca-alternativas" role="radiogroup" aria-label="Resultados da busca original">
                {alternativas.map((a) => (
                  <label key={a.indice} className="troca-alternativa">
                    <input type="radio" name={`${campoId}-alternativa`} checked={indice === a.indice} disabled={indisponivel} onChange={() => setIndice(a.indice)} />
                    {a.miniatura && <img src={a.miniatura} alt="" loading="lazy" />}
                    <span>{a.rotulo}{a.pagina && <> · <a href={a.pagina} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>ver origem</a></>}</span>
                  </label>
                ))}
              </div>
            ) : <p className="suave">Sem alternativas disponíveis. Faça uma nova busca ou envie um arquivo.</p>
          )}
          {modo === 'busca' && (
            <label className="troca-campo">Nova busca
              <input value={query} maxLength={80} disabled={indisponivel} onChange={(e) => setQuery(e.target.value)} />
            </label>
          )}
          {modo === 'upload' && (
            <label className="troca-campo">Arquivo {tipo === 'imagem' ? 'de imagem' : 'de vídeo'}
              <input type="file" accept={tipo === 'imagem' ? 'image/*' : 'video/*'} disabled={indisponivel} onChange={(e) => setArquivo(e.target.files?.[0] ?? null)} />
            </label>
          )}
          {erro && <p className="texto-erro" role="alert">{erro}</p>}
          <button type="submit" className="pequeno" disabled={indisponivel || !pronto}>
            {enviando ? 'iniciando troca…' : 'confirmar substituição'}
          </button>
        </form>
      )}
    </div>
  )
}
