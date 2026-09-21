import { useState } from 'react'
import { fmtSeg, urlDoClipe, type ClipOut } from '../api'
import ClipDetails, { type Aba } from './ClipDetails'

interface Props {
  projetoId: string
  clipes: ClipOut[]
  bloqueado: boolean
  onMover: (indice: number, delta: -1 | 1) => void
  onRemover: (clip: ClipOut) => void
}

export default function ClipList({ projetoId, clipes, bloqueado, onMover, onRemover }: Props) {
  // painel aberto: chave pelo arquivo (o índice muda ao reordenar)
  const [aberto, setAberto] = useState<{ arquivo: string; aba: Aba } | null>(null)

  if (clipes.length === 0) {
    return <p className="suave">Nenhum clipe. Importe uma pasta ou envie vídeos.</p>
  }

  const alternar = (clip: ClipOut, aba: Aba) =>
    setAberto((a) => (a && a.arquivo === clip.arquivo && a.aba === aba ? null : { arquivo: clip.arquivo, aba }))

  return (
    <ol className="clipes">
      {clipes.map((c, i) => {
        const painel = aberto && aberto.arquivo === c.arquivo ? aberto.aba : null
        return (
          <li key={`${c.arquivo}-${c.indice}`}>
            <div className="clipe">
              <img src={urlDoClipe(c.thumbnail_url, c)} alt="" className="miniatura" loading="lazy" />
              <div className="info">
                <strong>
                  {i + 1}. {c.nome}
                </strong>
                <span className="suave">
                  {fmtSeg(c.duracao)} · {c.largura ?? '?'}×{c.altura ?? '?'}
                  {c.fps ? ` @ ${c.fps.toFixed(2)} fps` : ''}
                </span>
                <span className="selos">
                  {c.transcrito && <span className="selo ok">transcrito</span>}
                  {c.rosto && <span className="selo ok">rosto</span>}
                  {c.tem_audio === false && <span className="selo aviso">sem áudio</span>}
                  {c.trechos.length > 0 && (
                    <span className="selo">
                      {c.trechos.length} trecho(s) · mantém {fmtSeg(c.duracao_mantida)}
                    </span>
                  )}
                </span>
                <span className="linha">
                  <button className={`pequeno ${painel === 'video' ? 'marcado' : ''}`} onClick={() => alternar(c, 'video')}>
                    ver vídeo
                  </button>
                  <button
                    className={`pequeno ${painel === 'transcricao' ? 'marcado' : ''}`}
                    onClick={() => alternar(c, 'transcricao')}
                    disabled={!c.transcrito}
                    title={c.transcrito ? '' : 'rode "Transcrever" primeiro'}
                  >
                    ver transcrição
                  </button>
                  <button
                    className={`pequeno ${painel === 'rosto' ? 'marcado' : ''}`}
                    onClick={() => alternar(c, 'rosto')}
                    disabled={!c.rosto}
                    title={c.rosto ? '' : 'rode "Rastrear rosto" primeiro'}
                  >
                    ver rosto
                  </button>
                </span>
              </div>
              <div className="ordem">
                <button className="pequeno" onClick={() => onMover(i, -1)} disabled={bloqueado || i === 0} title="subir">
                  ↑
                </button>
                <button
                  className="pequeno"
                  onClick={() => onMover(i, 1)}
                  disabled={bloqueado || i === clipes.length - 1}
                  title="descer"
                >
                  ↓
                </button>
                <button className="pequeno perigo" onClick={() => onRemover(c)} disabled={bloqueado} title="remover">
                  remover
                </button>
              </div>
            </div>
            {painel && <ClipDetails projetoId={projetoId} clip={c} aba={painel} />}
          </li>
        )
      })}
    </ol>
  )
}
