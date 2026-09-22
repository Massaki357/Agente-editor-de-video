import { api, fmtNum, fmtSeg, numeroDoResultado, type ClipOut, type Job, type ProjectOut } from '../api'
import type { EstadoPlano } from '../usePlano'
import ClipDetails, { type Aba } from './ClipDetails'
import ImagePlan from './ImagePlan'

export type ModoPalco = 'resultado' | 'clipe' | 'plano'

interface Props {
  projeto: ProjectOut
  modo: ModoPalco
  onModo: (modo: ModoPalco) => void
  /** clipe selecionado na faixa (null quando nenhum) */
  clipe: ClipOut | null
  aba: Aba
  onAba: (aba: Aba) => void
  /** último job 'gerar' concluído: dá o resumo do vídeo final */
  jobGerar: Job | null
  plano: EstadoPlano
  bloqueado: boolean
}

/** Área principal: resultado, clipe selecionado ou plano criativo. */
export default function Stage({ projeto, modo, onModo, clipe, aba, onAba, jobGerar, plano, bloqueado }: Props) {
  const temPlano = plano.dados !== null
  // só mostra as abas que fazem sentido agora
  const abas: { id: ModoPalco; rotulo: string }[] = [
    { id: 'resultado', rotulo: 'Resultado' },
    ...(clipe ? [{ id: 'clipe' as const, rotulo: `Clipe: ${clipe.nome}` }] : []),
    ...(temPlano ? [{ id: 'plano' as const, rotulo: 'Plano criativo' }] : []),
  ]
  const atual: ModoPalco = abas.some((a) => a.id === modo) ? modo : 'resultado'
  // a transcrição e o rosto podem não existir ainda: cai no vídeo
  const semDado = clipe ? (aba === 'transcricao' && !clipe.transcrito) || (aba === 'rosto' && !clipe.rosto) : false
  const abaClipe: Aba = semDado ? 'video' : aba

  return (
    <section className="palco" aria-label="Palco">
      <div className="abas" role="tablist" aria-label="Modo do palco">
        {abas.map((a) => (
          <button
            key={a.id}
            type="button"
            role="tab"
            id={`aba-${a.id}`}
            aria-selected={atual === a.id}
            aria-controls="palco-corpo"
            className={`aba${atual === a.id ? ' ativa' : ''}`}
            onClick={() => onModo(a.id)}
          >
            {a.rotulo}
          </button>
        ))}
      </div>

      <div className="palco-corpo" id="palco-corpo" role="tabpanel" aria-labelledby={`aba-${atual}`}>
        {atual === 'resultado' && <Resultado projeto={projeto} jobGerar={jobGerar} />}
        {atual === 'clipe' && clipe && (
          <>
            <div className="abas internas" role="tablist" aria-label="Painel do clipe">
              <AbaClipe id="video" atual={abaClipe} onAba={onAba} rotulo="vídeo" />
              <AbaClipe
                id="transcricao"
                atual={abaClipe}
                onAba={onAba}
                rotulo="transcrição"
                desabilitado={!clipe.transcrito}
                dica='rode "Transcrever" primeiro'
              />
              <AbaClipe
                id="rosto"
                atual={abaClipe}
                onAba={onAba}
                rotulo="rosto"
                desabilitado={!clipe.rosto}
                dica='rode "Rastrear rosto" primeiro'
              />
            </div>
            <ClipDetails
              key={`${clipe.arquivo}-${abaClipe}`}
              projetoId={projeto.id}
              clip={clipe}
              aba={abaClipe}
            />
          </>
        )}
        {atual === 'plano' && <ImagePlan plano={plano} bloqueado={bloqueado} />}
      </div>
    </section>
  )
}

interface AbaClipeProps {
  id: Aba
  atual: Aba
  onAba: (aba: Aba) => void
  rotulo: string
  desabilitado?: boolean
  dica?: string
}

function AbaClipe({ id, atual, onAba, rotulo, desabilitado, dica }: AbaClipeProps) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={atual === id}
      className={`aba pequena${atual === id ? ' ativa' : ''}`}
      onClick={() => onAba(id)}
      disabled={desabilitado}
      title={desabilitado ? dica : undefined}
    >
      {rotulo}
    </button>
  )
}

/** Player do vídeo final com o resumo do último render. */
function Resultado({ projeto, jobGerar }: { projeto: ProjectOut; jobGerar: Job | null }) {
  const url = projeto.video_final_url
  if (!url) {
    return (
      <div className="vazio">
        <h2>Sem vídeo ainda</h2>
        <p className="suave">
          Importe vídeos na faixa de clipes e clique em <strong>Gerar vídeo</strong>.
        </p>
      </div>
    )
  }
  const resultado = jobGerar?.resultado ?? null
  const duracao = numeroDoResultado(resultado, 'duracao_final')
  const imagens = numeroDoResultado(resultado, 'imagens')
  const zooms = numeroDoResultado(resultado, 'zooms')
  const removido = numeroDoResultado(resultado, 'removido_pct')

  return (
    <div className="resultado">
      <video key={url} src={url} controls preload="metadata" className="player vertical" />
      <div className="linha entre">
        <p className="suave numeros">
          {fmtSeg(duracao ?? projeto.duracao_total)}
          {imagens !== null ? ` · ${imagens} imagem(ns)` : ''}
          {zooms !== null ? ` · ${zooms} zoom(s)` : ''}
          {removido !== null ? ` · ${fmtNum(removido)}% removido` : ''}
        </p>
        <a className="botao" href={url} download>
          baixar
        </a>
      </div>
      {projeto.arquivos.length > 0 && (
        <details>
          <summary>Arquivos gerados ({projeto.arquivos.length})</summary>
          <ul className="compacta">
            {projeto.arquivos.map((a) => (
              <li key={a}>
                <a href={api.fileUrl(projeto.id, a)} target="_blank" rel="noreferrer">
                  {a}
                </a>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
