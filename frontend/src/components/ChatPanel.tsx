import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  api, fmtSeg, mensagemErro,
  type ChatAction, type ChatOut, type EditorPreview, type Job,
} from '../api'
import ErrorBox from './ErrorBox'

interface Props {
  projetoId: string
  versao: number
  jobs: Job[]
  bloqueado: boolean
  onSend: (message: string) => Promise<Job>
  onApply: (token: string) => Promise<Job>
  onSelectElement: (id: string) => void
}

const FERRAMENTAS: Record<string, string> = {
  listar_elementos: 'Consultou os elementos',
  remover_elemento: 'Removeu',
  trocar_imagem: 'Trocou a imagem',
  ajustar_duracao: 'Ajustou a duração',
  ajustar_intensidade_zoom: 'Ajustou o zoom',
  mover_elemento: 'Moveu',
}

function rotuloAcao(acao: ChatAction): string {
  const verbo = FERRAMENTAS[acao.ferramenta] ?? acao.ferramenta.replaceAll('_', ' ')
  return [verbo, acao.tipo, acao.id].filter(Boolean).join(' · ')
}

/** Conversa persistida pelo projeto; o MP4 final só muda depois de aplicar a prévia. */
export default function ChatPanel({ projetoId, versao, jobs, bloqueado, onSend, onApply, onSelectElement }: Props) {
  const [chat, setChat] = useState<ChatOut | null>(null)
  const [texto, setTexto] = useState('')
  const [carregando, setCarregando] = useState(true)
  const [enviando, setEnviando] = useState(false)
  const [erro, setErro] = useState<string | null>(null)
  const [pedidoLocal, setPedidoLocal] = useState<string | null>(null)
  const [chatJobId, setChatJobId] = useState<string | null>(null)
  const [applyJobId, setApplyJobId] = useState<string | null>(null)
  const fimRef = useRef<HTMLDivElement>(null)

  const carregar = useCallback(async () => {
    try {
      const dados = await api.getChat(projetoId)
      setChat(dados)
      setErro(null)
    } catch (e) {
      setErro(mensagemErro(e))
    } finally {
      setCarregando(false)
    }
  }, [projetoId])

  useEffect(() => { void carregar() }, [carregar, versao])

  const chatJob = jobs.find((j) => j.id === chatJobId)
  const applyJob = jobs.find((j) => j.id === applyJobId)

  useEffect(() => {
    if (!chatJob || !['concluido', 'erro', 'cancelado'].includes(chatJob.status)) return
    void carregar().finally(() => {
      if (chatJob.status !== 'concluido') setErro(chatJob.mensagem || 'Não foi possível concluir a conversa.')
      setPedidoLocal(null)
      setChatJobId(null)
    })
  }, [chatJob?.id, chatJob?.status, carregar])

  useEffect(() => {
    if (!applyJob || !['concluido', 'erro', 'cancelado'].includes(applyJob.status)) return
    void carregar().finally(() => {
      if (applyJob.status !== 'concluido') setErro(applyJob.mensagem || 'Não foi possível aplicar a edição.')
      setApplyJobId(null)
    })
  }, [applyJob?.id, applyJob?.status, carregar])

  useEffect(() => { fimRef.current?.scrollIntoView({ block: 'nearest' }) }, [chat?.messages.length, pedidoLocal])

  async function enviar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const message = texto.trim()
    if (!message || bloqueado || enviando || chatJobId) return
    setEnviando(true)
    setErro(null)
    try {
      const job = await onSend(message)
      setTexto('')
      setPedidoLocal(message)
      setChatJobId(job.id)
    } catch (e) {
      setErro(mensagemErro(e))
    } finally {
      setEnviando(false)
    }
  }

  async function aplicar(preview: EditorPreview) {
    if (bloqueado || applyJobId || chat?.pending_token !== preview.token) return
    setErro(null)
    try {
      const job = await onApply(preview.token)
      setApplyJobId(job.id)
    } catch (e) {
      setErro(mensagemErro(e))
    }
  }

  const esperando = chatJob && (chatJob.status === 'pendente' || chatJob.status === 'rodando')
  const aplicando = applyJob && (applyJob.status === 'pendente' || applyJob.status === 'rodando')
  const mensagens = chat?.messages ?? []
  const mostrarPedidoLocal = pedidoLocal !== null && mensagens.at(-1)?.text !== pedidoLocal

  return <section className="editor-chat" aria-label="Chat de ajustes">
    <div className="editor-chat-cabecalho">
      <h3>Peça um ajuste</h3>
      <p className="suave">Descreva o elemento e a mudança. Confira a prévia antes de aplicar ao vídeo.</p>
    </div>
    <div className="editor-chat-mensagens" aria-label="Histórico da conversa" aria-live="polite" aria-relevant="additions text">
      {carregando && !chat && <p className="suave" role="status">Carregando conversa…</p>}
      {!carregando && mensagens.length === 0 && <p className="suave">Exemplo: “tira o zoom da parte 2”.</p>}
      {mensagens.map((mensagem, indice) => {
        const preview = mensagem.preview
        const pendente = preview && chat?.pending_token === preview.token
        return <article className={`editor-chat-mensagem ${mensagem.role}`} key={indice}>
          <strong>{mensagem.role === 'user' ? 'Você' : 'Agente'}</strong>
          {mensagem.text && <p>{mensagem.text}</p>}
          {mensagem.actions && mensagem.actions.length > 0 && <ul className="editor-chat-acoes" aria-label="Ações do agente">
            {mensagem.actions.map((acao, i) => <li key={`${acao.ferramenta}-${acao.id}-${i}`}>
              {acao.id ? <button type="button" className="editor-chat-acao" onClick={() => onSelectElement(acao.id!)} title="Localizar na linha do tempo">{rotuloAcao(acao)}</button>
                : <span>{rotuloAcao(acao)}</span>}
            </li>)}
          </ul>}
          {preview && (pendente ? <div className="editor-chat-preview">
            <p className="suave numeros">Prévia · {fmtSeg(preview.inicio)}–{fmtSeg(preview.fim)} · sem áudio</p>
            <video key={preview.preview_url} src={preview.preview_url} controls muted playsInline preload="metadata" className="player vertical" aria-label="Prévia do ajuste pedido no chat" />
            <button type="button" className="primario" disabled={bloqueado || Boolean(applyJobId)} onClick={() => void aplicar(preview)}>
              {aplicando ? 'Aplicando…' : 'Aplicar ao vídeo final'}
            </button>
          </div> : <p className="suave">{mensagem.applied ? 'Edição aplicada ao vídeo final.' : 'Prévia encerrada.'}</p>)}
          {!preview && mensagem.applied && <p className="suave">Edição aplicada ao vídeo final.</p>}
        </article>
      })}
      {mostrarPedidoLocal && <article className="editor-chat-mensagem user"><strong>Você</strong><p>{pedidoLocal}</p></article>}
      {esperando && <p className="suave" role="status">Agente preparando resposta e prévia… {Math.round(chatJob.progresso * 100)}%</p>}
      {aplicando && <p className="suave" role="status">Aplicando edição… {Math.round(applyJob.progresso * 100)}%</p>}
      <div ref={fimRef} />
    </div>
    <div aria-live="polite"><ErrorBox erro={erro} onClose={() => setErro(null)} /></div>
    <form className="editor-chat-form" onSubmit={(event) => void enviar(event)}>
      <label htmlFor="editor-chat-texto">Seu pedido</label>
      <textarea id="editor-chat-texto" value={texto} maxLength={2000} rows={3}
        placeholder="Ex.: troque a imagem da cesta por outra"
        disabled={bloqueado || enviando || Boolean(chatJobId)}
        onChange={(event) => setTexto(event.target.value)} />
      <button type="submit" className="primario" disabled={bloqueado || enviando || Boolean(chatJobId) || !texto.trim()}>
        {enviando ? 'Enviando…' : 'Enviar pedido'}
      </button>
    </form>
  </section>
}
