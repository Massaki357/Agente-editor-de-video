import { useEffect, useState } from 'react'
import { api, fmtNum, fmtSeg, urlDoClipe, mensagemErro, type ClipOut, type RostoOut, type TranscricaoOut } from '../api'
import ErrorBox from './ErrorBox'

export type Aba = 'video' | 'transcricao' | 'rosto'

interface Props {
  projetoId: string
  clip: ClipOut
  aba: Aba
}

/** Conteúdo do palco no modo "clipe": player, transcrição ou rastreio de rosto. */
export default function ClipDetails({ projetoId, clip, aba }: Props) {
  if (aba === 'video') {
    return (
      <div className="detalhe">
        <video src={urlDoClipe(clip.video_url, clip)} controls preload="metadata" className="player" />
        <p className="suave">{clip.arquivo}</p>
      </div>
    )
  }
  if (aba === 'transcricao') return <Transcricao projetoId={projetoId} indice={clip.indice} />
  return <Rosto projetoId={projetoId} indice={clip.indice} />
}

/** Busca a transcrição ou o rosto do clipe (o painel é recriado ao trocar de clipe). */
function useCarregar<T>(buscar: (projetoId: string, indice: number) => Promise<T>, projetoId: string, indice: number) {
  const [dado, setDado] = useState<T | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  useEffect(() => {
    let vivo = true
    buscar(projetoId, indice)
      .then((d) => vivo && setDado(d))
      .catch((e) => vivo && setErro(mensagemErro(e)))
    return () => {
      vivo = false
    }
  }, [buscar, projetoId, indice])
  return { dado, erro }
}

function Transcricao({ projetoId, indice }: { projetoId: string; indice: number }) {
  const { dado, erro } = useCarregar<TranscricaoOut>(api.transcricao, projetoId, indice)
  if (erro)
    return (
      <div className="detalhe" aria-live="polite">
        <ErrorBox erro={erro} />
      </div>
    )
  if (!dado) return <div className="detalhe suave">carregando transcrição…</div>
  return (
    <div className="detalhe">
      <p className="suave">
        modelo {dado.modelo} · {fmtSeg(dado.duracao)} · {dado.palavras.length} palavras
      </p>
      <p className="texto">{dado.texto || <em>(sem fala)</em>}</p>
      <div className="rolagem">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>palavra</th>
              <th>início</th>
              <th>fim</th>
              <th>prob.</th>
            </tr>
          </thead>
          <tbody>
            {dado.palavras.map((p) => (
              <tr key={p.indice}>
                <td className="numeros">{p.indice}</td>
                <td>{p.texto}</td>
                <td className="numeros">{fmtNum(p.inicio, 2)}</td>
                <td className="numeros">{fmtNum(p.fim, 2)}</td>
                <td className="numeros">{fmtNum(p.prob, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Rosto({ projetoId, indice }: { projetoId: string; indice: number }) {
  const { dado, erro } = useCarregar<RostoOut>(api.rosto, projetoId, indice)
  if (erro)
    return (
      <div className="detalhe" aria-live="polite">
        <ErrorBox erro={erro} />
      </div>
    )
  if (!dado) return <div className="detalhe suave">carregando rastreio…</div>
  return (
    <div className="detalhe">
      <p>
        Cobertura: <strong>{fmtNum(dado.cobertura * 100)}%</strong> dos frames ·{' '}
        <span className="numeros">
          {dado.n_frames} frames · {dado.largura}×{dado.altura} @ {fmtNum(dado.fps, 2)} fps
        </span>
      </p>
      {dado.debug_url ? (
        <video src={dado.debug_url} controls preload="metadata" className="player" />
      ) : (
        <p className="suave">Sem vídeo de debug (rode "Rastrear rosto" neste projeto para gerar).</p>
      )}
    </div>
  )
}
