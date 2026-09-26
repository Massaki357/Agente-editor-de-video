import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  api, fmtSeg, mensagemErro,
  type BrollPreviewOut, type EditorAction, type EditorElement, type EditorOut,
  type EditorPreview, type Job, type PlanoOut,
} from '../api'
import ErrorBox from './ErrorBox'
import ChatPanel from './ChatPanel'

interface Props {
  projetoId: string
  versao: number
  jobs: Job[]
  bloqueado: boolean
  plano: PlanoOut | null
  broll: BrollPreviewOut | null
  onPreview: (elementoId: string, acao: EditorAction) => Promise<Job>
  onApply: (token: string) => Promise<Job>
  onChat: (message: string) => Promise<Job>
}

type Modo = 'alternativa' | 'busca' | 'upload' | 'remove'
const TIPOS: EditorElement['tipo'][] = ['imagem', 'broll', 'zoom', 'destaque']
const NOMES: Record<EditorElement['tipo'], string> = {
  imagem: 'Imagens', broll: 'B-roll', zoom: 'Zooms', destaque: 'Destaques',
}
const SINGULAR: Record<EditorElement['tipo'], string> = {
  imagem: 'Imagem', broll: 'B-roll', zoom: 'Zoom', destaque: 'Destaque',
}

/** Linha do tempo do vídeo final, com uma prévia curta antes de publicar a edição. */
export default function EditorView({ projetoId, versao, jobs, bloqueado, plano, broll, onPreview, onApply, onChat }: Props) {
  const [editor, setEditor] = useState<EditorOut | null>(null)
  const [carregando, setCarregando] = useState(true)
  const [erro, setErro] = useState<string | null>(null)
  const [selecionadoId, setSelecionadoId] = useState<string | null>(null)
  const [modo, setModo] = useState<Modo>('remove')
  const [query, setQuery] = useState('')
  const [indice, setIndice] = useState(0)
  const [arquivo, setArquivo] = useState<File | null>(null)
  const [enviando, setEnviando] = useState(false)
  const [previewJobId, setPreviewJobId] = useState<string | null>(null)
  const painelRef = useRef<HTMLElement>(null)

  useEffect(() => {
    let vivo = true
    setCarregando(true)
    api.getEditor(projetoId).then((dados) => {
      if (!vivo) return
      setEditor(dados)
      setSelecionadoId((id) => id && dados.elements.some((e) => e.id === id) ? id : null)
      setErro(null)
    }).catch((e: unknown) => { if (vivo) setErro(mensagemErro(e)) })
      .finally(() => { if (vivo) setCarregando(false) })
    return () => { vivo = false }
  }, [projetoId, versao])

  const selecionado = editor?.elements.find((e) => e.id === selecionadoId) ?? null
  const midia = selecionado?.tipo === 'imagem' || selecionado?.tipo === 'broll'
  const itemImagem = selecionado?.tipo === 'imagem'
    ? plano?.plano.itens.find((i) => `img_${String(i.id).padStart(3, '0')}` === selecionado.id)
    : undefined
  const itemBroll = selecionado?.tipo === 'broll'
    ? broll?.itens.find((i) => `broll_${String(i.id).padStart(3, '0')}` === selecionado.id)
    : undefined
  const alternativas = itemImagem
    ? itemImagem.candidatos.map((c, i) => ({ indice: i, rotulo: `${c.autor || 'autor desconhecido'} (${c.fonte})`, miniatura: c.miniatura, pagina: c.pagina }))
      .filter((a) => a.indice !== itemImagem.escolhida)
    : itemBroll
      ? (itemBroll.alternativas ?? []).filter((a) => a.id !== itemBroll.video_id)
        .map((a) => ({ indice: a.indice, rotulo: `${a.autor || 'autor desconhecido'} (${a.fonte})`, miniatura: '', pagina: a.pagina }))
      : []
  const queryAtual = itemImagem?.query ?? itemBroll?.query ?? ''
  const previewJob = jobs.find((j) => j.id === previewJobId)
  const resultado = previewJob?.status === 'concluido' ? previewJob.resultado as Partial<EditorPreview> | null : null
  const preview = resultado && typeof resultado.token === 'string' && typeof resultado.preview_url === 'string'
    && resultado.elemento === selecionadoId ? resultado as EditorPreview : null
  const indisponivel = bloqueado || enviando || !selecionado?.editavel
  const pronto = modo === 'remove' ? true
    : modo === 'busca' ? query.trim().length >= 2 && query.trim() !== queryAtual
      : modo === 'alternativa' ? alternativas.some((a) => a.indice === indice)
        : arquivo !== null

  function selecionar(elemento: EditorElement) {
    setSelecionadoId(elemento.id)
    setModo(elemento.tipo === 'imagem' || elemento.tipo === 'broll' ? 'busca' : 'remove')
    setQuery('')
    setIndice(0)
    setArquivo(null)
    setPreviewJobId(null)
    setErro(null)
  }

  function selecionarPorId(id: string) {
    const elemento = editor?.elements.find((item) => item.id === id)
    if (elemento) {
      selecionar(elemento)
      requestAnimationFrame(() => painelRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
    }
  }

  function mudarModo(novo: Modo) {
    setModo(novo)
    setPreviewJobId(null)
    setErro(null)
    if (novo === 'alternativa') setIndice(alternativas[0]?.indice ?? 0)
  }

  async function gerarPreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selecionado || !pronto || indisponivel) return
    const acao: EditorAction = modo === 'remove' ? { action: 'remove' }
      : modo === 'busca' ? { action: 'replace', mode: 'busca', query: query.trim() }
        : modo === 'alternativa' ? { action: 'replace', mode: 'alternativa', index: indice }
          : { action: 'replace', mode: 'upload', file: arquivo! }
    setEnviando(true)
    setErro(null)
    setPreviewJobId(null)
    try {
      const job = await onPreview(selecionado.id, acao)
      setPreviewJobId(job.id)
    } catch (e) {
      setErro(mensagemErro(e))
    } finally {
      setEnviando(false)
    }
  }

  async function aplicar() {
    if (!preview || indisponivel) return
    setEnviando(true)
    setErro(null)
    try {
      await onApply(preview.token)
      setPreviewJobId(null)
    } catch (e) {
      setErro(mensagemErro(e))
    } finally {
      setEnviando(false)
    }
  }

  if (carregando && !editor) return <p className="suave" role="status">Carregando linha do tempo…</p>
  if (!editor) return <ErrorBox erro={erro} />
  const duracao = Math.max(editor.duration, 0.001)
  const elementos = [...editor.elements].sort((a, b) => a.inicio - b.inicio || a.id.localeCompare(b.id))

  return (
    <div className="editor-pos-render">
      <div className="linha entre">
        <div>
          <h2>Edição do vídeo</h2>
          <p className="suave">Selecione um elemento, confira a prévia e aplique a mudança ao vídeo final.</p>
        </div>
        <button type="button" className="pequeno" disabled={carregando || bloqueado} onClick={() => {
          setCarregando(true)
          api.getEditor(projetoId).then(setEditor).catch((e: unknown) => setErro(mensagemErro(e))).finally(() => setCarregando(false))
        }}>recarregar</button>
      </div>
      <div aria-live="polite"><ErrorBox erro={erro} onClose={() => setErro(null)} /></div>
      <div className="editor-layout">
      <div className="editor-conteudo">
      {elementos.length === 0 ? <p className="suave">Nenhum elemento editável foi encontrado no vídeo gerado.</p> : (
        <>
          <div className="editor-timeline" aria-label="Linha do tempo do vídeo final">
            <div className="editor-regua" aria-hidden="true">
              {[0, .25, .5, .75, 1].map((parte) => <span key={parte}>{fmtSeg(editor.duration * parte)}</span>)}
            </div>
            {TIPOS.map((tipo) => (
              <div className="editor-pista" key={tipo}>
                <span className="editor-pista-nome">{NOMES[tipo]}</span>
                <div className="editor-pista-faixa">
                  {elementos.filter((e) => e.tipo === tipo).map((e) => {
                    const inicio = Math.max(0, Math.min(100, e.inicio / duracao * 100))
                    const fim = Math.max(inicio, Math.min(100, e.fim / duracao * 100))
                    return <button
                      key={e.id} type="button" className={`editor-marca editor-marca-${tipo}${e.ativo ? '' : ' inativa'}${selecionadoId === e.id ? ' selecionada' : ''}`}
                      style={{ left: `${inicio}%`, width: `${Math.max(1.4, Math.min(100 - inicio, fim - inicio))}%` }}
                      aria-label={`${NOMES[tipo]}: ${e.rotulo}, ${fmtSeg(e.inicio)} a ${fmtSeg(e.fim)}${e.ativo ? '' : ', inativo'}`}
                      aria-pressed={selecionadoId === e.id}
                      title={`${e.rotulo} · ${fmtSeg(e.inicio)}–${fmtSeg(e.fim)}`}
                      onClick={() => selecionar(e)}
                    ><span className="oculto">{e.rotulo}</span></button>
                  })}
                </div>
              </div>
            ))}
          </div>
          <div className="editor-elementos" aria-label="Elementos na ordem do vídeo">
            {elementos.map((e) => <button key={e.id} type="button" className={`pequeno${selecionadoId === e.id ? ' editor-escolhido' : ''}`}
              aria-pressed={selecionadoId === e.id} onClick={() => selecionar(e)}>
              {SINGULAR[e.tipo]} · {fmtSeg(e.inicio)} · {e.rotulo}
            </button>)}
          </div>
        </>
      )}
      {selecionado && <section ref={painelRef} className="editor-painel" aria-label={`Editar ${selecionado.rotulo}`}>
        <div>
          <h3>{selecionado.rotulo}</h3>
          <p className="suave numeros">{NOMES[selecionado.tipo]} · {fmtSeg(selecionado.inicio)}–{fmtSeg(selecionado.fim)}{selecionado.ativo ? '' : ' · inativo'}</p>
        </div>
        {!selecionado.editavel ? <p className="texto-aviso">Este elemento não pode ser editado no vídeo atual.</p> : <>
          <form onSubmit={(event) => void gerarPreview(event)}>
            {midia && <fieldset disabled={indisponivel}>
              <legend>Ação</legend>
              <div className="linha editor-opcoes">
                <label><input type="radio" name="editor-modo" checked={modo === 'alternativa'} disabled={alternativas.length === 0} onChange={() => mudarModo('alternativa')} /> alternativa salva</label>
                <label><input type="radio" name="editor-modo" checked={modo === 'busca'} onChange={() => mudarModo('busca')} /> nova busca</label>
                <label><input type="radio" name="editor-modo" checked={modo === 'upload'} onChange={() => mudarModo('upload')} /> meu arquivo</label>
                <label><input type="radio" name="editor-modo" checked={modo === 'remove'} onChange={() => mudarModo('remove')} /> remover</label>
              </div>
            </fieldset>}
            {modo === 'alternativa' && midia && <div className="troca-alternativas" role="radiogroup" aria-label="Alternativas da busca original">
              {alternativas.map((a) => <label key={a.indice} className="troca-alternativa">
                <input type="radio" name="editor-alternativa" checked={indice === a.indice} disabled={indisponivel} onChange={() => { setIndice(a.indice); setPreviewJobId(null) }} />
                {a.miniatura && <img src={a.miniatura} alt="" loading="lazy" />}
                <span>{a.rotulo}{a.pagina && <> · <a href={a.pagina} target="_blank" rel="noreferrer" onClick={(ev) => ev.stopPropagation()}>ver origem</a></>}</span>
              </label>)}
            </div>}
            {modo === 'busca' && midia && <label className="troca-campo">Nova busca
              <input value={query} maxLength={80} disabled={indisponivel} onChange={(ev) => { setQuery(ev.target.value); setPreviewJobId(null) }} placeholder={queryAtual || 'Descreva a mídia desejada'} />
            </label>}
            {modo === 'upload' && midia && <label className="troca-campo">{selecionado.tipo === 'imagem' ? 'Imagem' : 'Vídeo'} do computador
              <input type="file" accept={selecionado.tipo === 'imagem' ? 'image/*' : 'video/*'} disabled={indisponivel} onChange={(ev) => { setArquivo(ev.target.files?.[0] ?? null); setPreviewJobId(null) }} />
            </label>}
            {modo === 'remove' && <p className="suave">A prévia mostra o trecho sem este elemento.</p>}
            <button type="submit" className="primario" disabled={indisponivel || !pronto}>
              {enviando ? 'iniciando prévia…' : 'Gerar prévia do trecho'}
            </button>
          </form>
          {previewJob && (previewJob.status === 'pendente' || previewJob.status === 'rodando') && <p className="suave" role="status">Preparando prévia… {Math.round(previewJob.progresso * 100)}%</p>}
          {previewJob && (previewJob.status === 'erro' || previewJob.status === 'cancelado') && <p className="texto-erro" role="alert">{previewJob.mensagem || 'Não foi possível gerar a prévia.'}</p>}
          {preview && <div className="editor-preview">
            <h4>Prévia do trecho editado</h4>
            <p className="suave numeros">{fmtSeg(preview.inicio)}–{fmtSeg(preview.fim)} · resolução reduzida, sem áudio</p>
            <video key={preview.preview_url} src={preview.preview_url} controls muted playsInline preload="metadata" className="player vertical" aria-label="Prévia da edição" />
            <button type="button" className="primario" disabled={indisponivel} onClick={() => void aplicar()}>Aplicar ao vídeo final</button>
          </div>}
        </>}
      </section>}
      </div>
      <ChatPanel projetoId={projetoId} versao={versao} jobs={jobs} bloqueado={bloqueado}
        onSend={onChat} onApply={onApply} onSelectElement={selecionarPorId} />
      </div>
    </div>
  )
}
