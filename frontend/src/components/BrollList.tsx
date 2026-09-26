import { useEffect, useState } from 'react'
import { fmtSeg, type BrollEdit, type BrollItemOut, type Substituicao, type TransitionConfig, type TransitionId, type TransitionPreset } from '../api'
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
      <section className="transicoes-catalogo" aria-label="Catálogo de transições">
        <h4>Compare as transições</h4>
        <p className="suave">As prévias mostram entrada e saída com as mesmas cenas e áudio. Custo estimado por efeito:</p>
        {broll.carregandoCatalogo && !broll.catalogo && <p className="suave">carregando catálogo…</p>}
        {broll.erroCatalogo && <p className="texto-aviso" role="alert">Catálogo indisponível: {broll.erroCatalogo}</p>}
        {broll.catalogo && <div className="transicoes-grade">
          {broll.catalogo.presets.map((preset) => <article className="transicao-previsao" key={preset.id}>
            <video
              src={preset.preview_url}
              controls
              preload="none"
              playsInline
              aria-label={`Prévia de ${preset.nome}: entrada e saída`}
            />
            <strong>{preset.nome}</strong>
            <p>{preset.descricao}</p>
            <p className="suave">Custo: {preset.custo} · {fmtSeg(preset.duracao_padrao)}</p>
          </article>)}
        </div>}
      </section>
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
            presets={broll.catalogo?.presets ?? []}
            temVideo={temVideo}
            onSubstituir={onSubstituir}
          />
        ))}
      </div>
    </section>
  )
}

function BrollCard({ item, desabilitado, salvando, onEditar, presets, temVideo, onSubstituir }: {
  item: BrollItemOut
  desabilitado: boolean
  salvando: boolean
  onEditar: (mudanca: BrollEdit) => void
  presets: TransitionPreset[]
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
        {presets.length > 0 && <div className="transicoes-item">
          <TransitionSide key={`entrada-${JSON.stringify(item.transicao_entrada ?? null)}`} lado="entrada" item={item} presets={presets} desabilitado={desabilitado} onEditar={onEditar} />
          <TransitionSide key={`saida-${JSON.stringify(item.transicao_saida ?? null)}`} lado="saida" item={item} presets={presets} desabilitado={desabilitado} onEditar={onEditar} />
        </div>}
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

const DIRECOES = { left: 'esquerda', right: 'direita', up: 'cima', down: 'baixo' }

function TransitionSide({ lado, item, presets, desabilitado, onEditar }: {
  lado: 'entrada' | 'saida'
  item: BrollItemOut
  presets: TransitionPreset[]
  desabilitado: boolean
  onEditar: (mudanca: BrollEdit) => void
}) {
  const salvo = (lado === 'entrada' ? item.transicao_entrada : item.transicao_saida) ?? null
  const [rascunho, setRascunho] = useState<TransitionConfig | null>(salvo)
  const preset = presets.find((p) => p.id === rascunho?.preset)
  const duracao = preset ? rascunho?.duration ?? preset.duracao_padrao : 0
  const direcao = preset ? rascunho?.direction ?? preset.direcao_padrao : null
  const intensidade = preset ? rascunho?.intensity ?? preset.intensidade_padrao : 1
  const valido = !rascunho || Boolean(preset
    && Number.isFinite(duracao) && duracao >= preset.duracao_min && duracao <= preset.duracao_max
    && (direcao === null || preset.direcoes.includes(direcao))
    && (preset.direcoes.length === 0 || direcao !== null)
    && preset.intensidades.includes(intensidade))
  const mudou = JSON.stringify(rascunho) !== JSON.stringify(salvo)
  const rotulo = lado === 'entrada' ? 'Entrada' : 'Saída'
  const prefixo = `broll-${item.id}-${lado}`

  return <fieldset className="transicao-lado" disabled={desabilitado}>
    <legend>{rotulo} do B-roll</legend>
    <div className="campo">
      <label htmlFor={`${prefixo}-preset`}>efeito</label>
      <select
        id={`${prefixo}-preset`}
        value={rascunho?.preset ?? ''}
        onChange={(e) => {
          const id = e.target.value as TransitionId | ''
          const escolhido = presets.find((p) => p.id === id)
          setRascunho(escolhido ? {
            preset: escolhido.id,
            duration: escolhido.duracao_padrao,
            direction: escolhido.direcao_padrao,
            intensity: escolhido.intensidade_padrao,
          } : null)
        }}
      >
        <option value="">padrão do projeto</option>
        {presets.map((p) => <option key={p.id} value={p.id}>{p.nome}</option>)}
      </select>
    </div>
    {preset && <>
      <div className="campo">
        <label htmlFor={`${prefixo}-duracao`}>duração (s)</label>
        <input
          id={`${prefixo}-duracao`}
          type="number"
          min={preset.duracao_min}
          max={preset.duracao_max}
          step="0.01"
          value={duracao}
          disabled={preset.duracao_max === 0}
          onChange={(e) => setRascunho((atual) => atual ? {
            ...atual, duration: e.target.value === '' ? null : Number(e.target.value),
          } : null)}
        />
        <span className="suave">{preset.duracao_min}–{preset.duracao_max} s</span>
      </div>
      {preset.direcoes.length > 0 && <div className="campo">
        <label htmlFor={`${prefixo}-direcao`}>direção</label>
        <select
          id={`${prefixo}-direcao`}
          value={direcao ?? ''}
          onChange={(e) => setRascunho((atual) => atual ? {
            ...atual, direction: e.target.value as TransitionConfig['direction'],
          } : null)}
        >
          {preset.direcoes.map((d) => <option key={d} value={d}>{DIRECOES[d]}</option>)}
        </select>
      </div>}
      {preset.intensidades.length > 1 && <div className="campo">
        <label htmlFor={`${prefixo}-intensidade`}>intensidade</label>
        <select
          id={`${prefixo}-intensidade`}
          value={intensidade}
          onChange={(e) => setRascunho((atual) => atual ? {
            ...atual, intensity: Number(e.target.value),
          } : null)}
        >
          {preset.intensidades.map((n) => <option key={n} value={n}>{n === 0.5 ? 'suave' : n === 1 ? 'normal' : n === 1.5 ? 'marcada' : `${n}×`}</option>)}
        </select>
      </div>}
      <p className="suave">Custo: {preset.custo}</p>
    </>}
    <button
      type="button"
      className="pequeno"
      disabled={desabilitado || !mudou || !valido}
      onClick={() => onEditar(lado === 'entrada' ? { transicao_entrada: rascunho } : { transicao_saida: rascunho })}
    >salvar {lado === 'entrada' ? 'entrada' : 'saída'}</button>
    {!valido && <p className="texto-aviso" role="alert">Ajuste os parâmetros aos limites do efeito.</p>}
  </fieldset>
}
