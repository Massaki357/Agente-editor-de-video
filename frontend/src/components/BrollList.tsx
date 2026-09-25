import { useEffect, useState } from 'react'
import { fmtSeg, type BrollEdit, type BrollItemOut, type Substituicao } from '../api'
import type { EstadoBroll } from '../useBroll'
import ErrorBox from './ErrorBox'
import ReplacePanel from './ReplacePanel'

interface Props {
  broll: EstadoBroll
  bloqueado: boolean
  temVideo: boolean
  onSubstituir: (id: string, troca: Substituicao) => Promise<void>
}

/** Revisão dos vídeos de apoio dentro do plano criativo. */
export default function BrollList({ broll, bloqueado, temVideo, onSubstituir }: Props) {
  const itens = broll.dados?.itens ?? []
  const aprovados = itens.filter((item) => item.ativo && item.aprovado).length
  const ocupado = bloqueado || broll.salvandoItem !== null || !broll.dados?.valido

  return (
    <section className="broll-lista" aria-label="Vídeos de apoio">
      <div className="linha entre">
        <h3 className="secao">B-roll <span className="suave">{itens.length} sugestão(ões), {aprovados} aprovada(s)</span></h3>
        <button type="button" className="pequeno" onClick={broll.recarregar} disabled={broll.carregando}>recarregar</button>
      </div>
      <p className="suave">Confira se cada vídeo combina com a frase. Só os aprovados entram no vídeo final.</p>
      <div aria-live="polite"><ErrorBox erro={broll.erro} onClose={broll.limparErro} /></div>
      {broll.carregando && !broll.dados && <p className="suave">carregando B-roll…</p>}
      {broll.semPrevia && <p className="suave">Nenhuma prévia de B-roll ainda. Ative B-roll e clique em Preparar B-roll.</p>}
      {broll.dados && !broll.dados.valido && <p className="texto-aviso">Os cortes mudaram; prepare o B-roll novamente antes de editar.</p>}
      {broll.dados && itens.length === 0 && <p className="suave">Nenhum vídeo de apoio foi sugerido para este trecho.</p>}
      <div className="imagens">
        {itens.map((item) => (
          <BrollCard
            key={item.id}
            item={item}
            desabilitado={ocupado}
            salvando={broll.salvandoItem === item.id}
            onEditar={(mudanca) => broll.editar(item, mudanca)}
            temVideo={temVideo}
            onSubstituir={onSubstituir}
          />
        ))}
      </div>
    </section>
  )
}

function BrollCard({ item, desabilitado, salvando, onEditar, temVideo, onSubstituir }: {
  item: BrollItemOut
  desabilitado: boolean
  salvando: boolean
  onEditar: (mudanca: BrollEdit) => void
  temVideo: boolean
  onSubstituir: (id: string, troca: Substituicao) => Promise<void>
}) {
  const [query, setQuery] = useState(item.query)
  useEffect(() => setQuery(item.query), [item.query])
  const busca = query.trim()
  const semVideo = item.ativo && !item.video_url

  return (
    <article className={`imagem-item broll-item${item.ativo ? '' : ' inativa'}`}>
      <div className="broll-video">
        {item.video_url ? (
          <video key={item.video_url} src={item.video_url} controls preload="metadata" playsInline />
        ) : <span className="suave">vídeo indisponível</span>}
      </div>
      <div className="imagem-corpo">
        <p><strong>“{item.texto}”</strong></p>
        <p className="suave numeros">{fmtSeg(item.inicio)} – {fmtSeg(item.inicio + item.duracao)}</p>
        <div className="linha">
          <label className="caixa">
            <input type="checkbox" checked={item.ativo} disabled={desabilitado} onChange={(e) => onEditar({ ativo: e.target.checked })} />
            usar cutaway
          </label>
          <label className="caixa" title={semVideo ? 'prepare a prévia para aprovar' : undefined}>
            <input type="checkbox" checked={item.aprovado} disabled={desabilitado || !item.ativo || !item.video_url} onChange={(e) => onEditar({ aprovado: e.target.checked })} />
            aprovado
          </label>
          {salvando && <span className="texto-aviso">salvando…</span>}
        </div>
        <form className="linha" onSubmit={(e) => { e.preventDefault(); if (busca.length >= 2 && busca !== item.query) onEditar({ query: busca }) }}>
          <label className="oculto" htmlFor={`broll-busca-${item.id}`}>Busca do vídeo para “{item.texto}”</label>
          <input id={`broll-busca-${item.id}`} value={query} maxLength={80} disabled={desabilitado} onChange={(e) => setQuery(e.target.value)} />
          <button type="submit" className="pequeno" disabled={desabilitado || busca.length < 2 || busca === item.query}>trocar busca</button>
        </form>
        {semVideo && <p className="texto-aviso">Clique em Preparar B-roll para buscar este vídeo.</p>}
        {item.video_url && item.pagina && (
          <p className="suave">vídeo: <a href={item.pagina} target="_blank" rel="noreferrer">{item.autor || 'autor desconhecido'} ({item.fonte || 'fonte'})</a></p>
        )}
        {temVideo && <ReplacePanel
          key={`${item.id}-${item.video_id}-${(item.alternativas ?? []).map((a) => a.id).join('-')}`}
          elementoId={`broll_${String(item.id).padStart(3, '0')}`}
          tipo="vídeo"
          queryAtual={item.query}
          alternativas={(item.alternativas ?? []).filter((a) => a.id !== item.video_id).map((a) => ({ indice: a.indice, rotulo: `${a.autor || 'autor desconhecido'} (${a.fonte})`, pagina: a.pagina }))}
          bloqueado={desabilitado}
          onSubstituir={onSubstituir}
        />}
      </div>
    </article>
  )
}
