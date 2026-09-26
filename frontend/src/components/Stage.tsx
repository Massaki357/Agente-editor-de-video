import type { KeyboardEvent } from 'react'
import { api, fmtNum, fmtSeg, numeroDoResultado, type ClipOut, type EditorAction, type HistoryOut, type Job, type ProjectOut, type Substituicao } from '../api'
import type { EstadoPlano } from '../usePlano'
import type { EstadoBroll } from '../useBroll'
import ClipDetails, { type Aba } from './ClipDetails'
import ImagePlan from './ImagePlan'
import HistoryControls from './HistoryControls'
import EditorView from './EditorView'

export type ModoPalco = 'resultado' | 'clipe' | 'plano' | 'edicao'

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
  jobSubstituir: Job | null
  jobHistorico: Job | null
  jobEdicao: Job | null
  historico: HistoryOut | null
  erroHistorico: string | null
  plano: EstadoPlano
  broll: EstadoBroll
  bloqueado: boolean
  onSubstituir: (id: string, troca: Substituicao) => Promise<void>
  onDesfazer: () => void
  onRefazer: () => void
  jobs: Job[]
  versaoEditor: number
  onPreview: (id: string, acao: EditorAction) => Promise<Job>
  onAplicar: (token: string) => Promise<Job>
  onChat: (message: string) => Promise<Job>
}

/** Área principal: resultado, clipe selecionado ou plano criativo. */
export default function Stage({ projeto, modo, onModo, clipe, aba, onAba, jobGerar, jobSubstituir, jobHistorico, jobEdicao, historico, erroHistorico, plano, broll, bloqueado, onSubstituir, onDesfazer, onRefazer, jobs, versaoEditor, onPreview, onAplicar, onChat }: Props) {
  const temPlano = plano.dados !== null || broll.dados !== null
  // só mostra as abas que fazem sentido agora
  const abas: { id: ModoPalco; rotulo: string }[] = [
    { id: 'resultado', rotulo: 'Resultado' },
    ...(clipe ? [{ id: 'clipe' as const, rotulo: `Clipe: ${clipe.nome}` }] : []),
    ...(temPlano ? [{ id: 'plano' as const, rotulo: 'Plano criativo' }] : []),
    ...(projeto.video_final_url ? [{ id: 'edicao' as const, rotulo: 'Edição' }] : []),
  ]
  const atual: ModoPalco = abas.some((a) => a.id === modo) ? modo : 'resultado'
  // a transcrição e o rosto podem não existir ainda: cai no vídeo
  const semDado = clipe ? (aba === 'transcricao' && !clipe.transcrito) || (aba === 'rosto' && !clipe.rosto) : false
  const abaClipe: Aba = semDado ? 'video' : aba

  return (
    <section className="palco" aria-label="Palco">
      <div className="abas" role="tablist" aria-label="Modo do palco" onKeyDown={navegarAbas}>
        {abas.map((a) => (
          <button
            key={a.id}
            type="button"
            role="tab"
            id={`aba-${a.id}`}
            aria-selected={atual === a.id}
            aria-controls="palco-corpo"
            tabIndex={atual === a.id ? 0 : -1}
            className={`aba${atual === a.id ? ' ativa' : ''}`}
            onClick={() => onModo(a.id)}
          >
            {a.rotulo}
          </button>
        ))}
      </div>

      <div className="palco-corpo" id="palco-corpo" role="tabpanel" aria-labelledby={`aba-${atual}`}>
        {atual === 'resultado' && <Resultado projeto={projeto} jobGerar={jobGerar} jobSubstituir={jobSubstituir} jobHistorico={jobHistorico} jobEdicao={jobEdicao} historico={historico} erroHistorico={erroHistorico} bloqueado={bloqueado} onDesfazer={onDesfazer} onRefazer={onRefazer} />}
        {atual === 'clipe' && clipe && (
          <>
            <div
              className="abas internas"
              role="tablist"
              aria-label="Painel do clipe"
              onKeyDown={navegarAbas}
            >
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
            <div id="painel-clipe" role="tabpanel" aria-labelledby={`aba-clipe-${abaClipe}`}>
              <ClipDetails
                key={`${clipe.arquivo}-${abaClipe}`}
                projetoId={projeto.id}
                clip={clipe}
                aba={abaClipe}
              />
            </div>
          </>
        )}
        {atual === 'plano' && <ImagePlan
          plano={plano} broll={broll} bloqueado={bloqueado}
          temVideo={Boolean(projeto.video_final_url)}
          imagensHabilitadas={projeto.pode_substituir_imagens}
          brollHabilitado={projeto.pode_substituir_broll}
          onSubstituir={onSubstituir}
        />}
        {atual === 'edicao' && <EditorView
          projetoId={projeto.id} versao={versaoEditor} jobs={jobs} bloqueado={bloqueado}
          plano={plano.dados} broll={broll.dados}
          onPreview={onPreview} onApply={onAplicar} onChat={onChat}
        />}
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
      id={`aba-clipe-${id}`}
      aria-selected={atual === id}
      aria-controls="painel-clipe"
      tabIndex={atual === id ? 0 : -1}
      className={`aba pequena${atual === id ? ' ativa' : ''}`}
      onClick={() => onAba(id)}
      disabled={desabilitado}
      title={desabilitado ? dica : undefined}
    >
      {rotulo}
    </button>
  )
}

/** Setas ← →, Home e End andam pelas abas do tablist (a seleção segue o foco). */
function navegarAbas(ev: KeyboardEvent<HTMLDivElement>) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(ev.key)) return
  const abas = Array.from(ev.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)'))
  if (abas.length === 0) return
  const atual = abas.indexOf(document.activeElement as HTMLButtonElement)
  let destino: number
  if (ev.key === 'Home') destino = 0
  else if (ev.key === 'End') destino = abas.length - 1
  else if (atual < 0) destino = 0
  else destino = (atual + (ev.key === 'ArrowRight' ? 1 : -1) + abas.length) % abas.length
  ev.preventDefault()
  abas[destino].focus()
  abas[destino].click() // ativação automática: mostrar a aba focada
}

/** Player do vídeo final com o resumo do último render. */
function Resultado({ projeto, jobGerar, jobSubstituir, jobHistorico, jobEdicao, historico, erroHistorico, bloqueado, onDesfazer, onRefazer }: {
  projeto: ProjectOut
  jobGerar: Job | null
  jobSubstituir: Job | null
  jobHistorico: Job | null
  jobEdicao: Job | null
  historico: HistoryOut | null
  erroHistorico: string | null
  bloqueado: boolean
  onDesfazer: () => void
  onRefazer: () => void
}) {
  const original = projeto.video_final_url
  const versao = [jobGerar, jobSubstituir, jobHistorico, jobEdicao].filter((j): j is Job => j !== null).map((j) => j.terminado ?? j.criado).sort().at(-1)
  const url = original ? `${original}${original.includes('?') ? '&' : '?'}v=${encodeURIComponent(versao ?? projeto.atualizado)}` : null
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
  const broll = numeroDoResultado(resultado, 'broll')
  const removido = numeroDoResultado(resultado, 'removido_pct')
  const ultimaEdicao = [jobSubstituir, jobHistorico, jobEdicao]
    .filter((j): j is Job => j !== null)
    .sort((a, b) => b.criado.localeCompare(a.criado))[0]

  return (
    <div className="resultado">
      <video key={url} src={url} controls preload="metadata" className="player vertical" />
      {ultimaEdicao && (!jobGerar || ultimaEdicao.criado > jobGerar.criado) && (
        <p className="suave" role="status">{ultimaEdicao.tipo === 'substituir' ? 'Substituição concluída' : ultimaEdicao.tipo === 'desfazer' ? 'Edição desfeita' : ultimaEdicao.tipo === 'refazer' ? 'Edição refeita' : 'Edição aplicada'}. O vídeo final foi atualizado.</p>
      )}
      <HistoryControls history={historico} erro={erroHistorico} bloqueado={bloqueado} onDesfazer={onDesfazer} onRefazer={onRefazer} />
      <div className="linha entre">
        <p className="suave numeros">
          {fmtSeg(duracao ?? projeto.duracao_total)}
          {imagens !== null ? ` · ${imagens} imagem(ns)` : ''}
          {zooms !== null ? ` · ${zooms} zoom(s)` : ''}
          {broll !== null ? ` · ${broll} cutaway(s)` : ''}
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
