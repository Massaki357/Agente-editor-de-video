import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, fmtSeg, mensagemErro, type ImagemEdit, type ItemImagem, type ItemZoom, type PlanoOut } from '../api'
import ErrorBox from './ErrorBox'

interface Props {
  projetoId: string
  bloqueado: boolean
  /** muda quando um job 'imagens' ou 'gerar' termina: recarrega o plano */
  versao: number
}

export default function ImagePlan({ projetoId, bloqueado, versao }: Props) {
  const [dados, setDados] = useState<PlanoOut | null>(null)
  const [semPlano, setSemPlano] = useState(false)
  const [carregando, setCarregando] = useState(true)
  const [erro, setErro] = useState<string | null>(null)
  const [salvando, setSalvando] = useState<number | null>(null) // id do item sendo alterado
  const [salvandoZoom, setSalvandoZoom] = useState<number | null>(null) // id do zoom sendo alterado

  const carregar = useCallback(async () => {
    setCarregando(true)
    try {
      setDados(await api.imagens(projetoId))
      setSemPlano(false)
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setDados(null)
        setSemPlano(true)
      } else {
        setErro(mensagemErro(e))
      }
    } finally {
      setCarregando(false)
    }
  }, [projetoId])

  useEffect(() => {
    void carregar()
  }, [carregar, versao])

  const editar = async (item: ItemImagem, mudanca: ImagemEdit) => {
    setSalvando(item.id)
    setErro(null)
    try {
      setDados(await api.editImagem(projetoId, item.id, mudanca))
      return true
    } catch (e) {
      setErro(`imagem "${item.palavra}": ${mensagemErro(e)}`)
      return false
    } finally {
      setSalvando(null)
    }
  }

  const alternarZoom = async (zoom: ItemZoom, ativo: boolean) => {
    setSalvandoZoom(zoom.id)
    setErro(null)
    try {
      setDados(await api.setZoomActive(projetoId, zoom.id, ativo))
    } catch (e) {
      setErro(`zoom "${zoom.palavra}": ${mensagemErro(e)}`)
    } finally {
      setSalvandoZoom(null)
    }
  }

  const itens = dados?.plano.itens ?? []
  const zooms = dados?.plano.zooms ?? []
  const zoomsAtivos = zooms.filter((z) => z.ativo).length
  const ocupado = bloqueado || salvando !== null || salvandoZoom !== null
  const ativas = itens.filter((i) => i.ativa && i.candidatos.length > 0).length

  return (
    <section className="cartao">
      <div className="linha">
        <h3>Plano criativo: imagens e zooms (preview)</h3>
        <button className="link" onClick={() => void carregar()} disabled={carregando}>
          recarregar
        </button>
      </div>
      <p className="suave">
        Trocar fotos ou ligar/desligar zooms aqui não chama o LLM de novo; depois clique em Gerar vídeo.
      </p>
      <ErrorBox erro={erro} onClose={() => setErro(null)} />
      {carregando && !dados && <p className="suave">carregando plano…</p>}
      {semPlano && (
        <p className="suave">Nenhum plano criativo ainda. Use "Sugerir imagens e zooms (preview)" ou "Gerar vídeo".</p>
      )}
      {dados && !dados.valido && (
        <p className="aviso-texto">Os cortes mudaram; o próximo "Gerar vídeo" refaz o plano.</p>
      )}
      {dados && <h4>Imagens</h4>}
      {dados && (
        <p className="suave">
          {itens.length} item(ns), {ativas} em uso
        </p>
      )}
      {dados && itens.length === 0 && <p className="suave">O LLM não sugeriu nenhuma imagem.</p>}
      <div className="imagens">
        {itens.map((item) => (
          <ItemCartao
            key={item.id}
            item={item}
            desabilitado={ocupado}
            salvando={salvando === item.id}
            onEditar={(m) => editar(item, m)}
          />
        ))}
      </div>
      {dados && (
        <ZoomLista
          zooms={zooms}
          ativos={zoomsAtivos}
          desabilitado={ocupado}
          salvandoId={salvandoZoom}
          onAlternar={(z, ativo) => void alternarZoom(z, ativo)}
        />
      )}
    </section>
  )
}

interface ZoomListaProps {
  zooms: ItemZoom[]
  ativos: number
  desabilitado: boolean
  salvandoId: number | null
  onAlternar: (zoom: ItemZoom, ativo: boolean) => void
}

function ZoomLista({ zooms, ativos, desabilitado, salvandoId, onAlternar }: ZoomListaProps) {
  return (
    <>
      <h4>Zooms no rosto</h4>
      <p className="suave">
        {zooms.length} zoom(s), {ativos} em uso. Só entram no vídeo com o vertical 9:16 e "zooms no rosto" ligados.
      </p>
      {zooms.length === 0 && <p className="suave">O LLM não sugeriu nenhum zoom.</p>}
      {zooms.length > 0 && (
        <ul className="zooms">
          {zooms.map((z) => (
            <li key={z.id} className={z.ativo ? '' : 'inativa'}>
              <label>
                <input
                  type="checkbox"
                  checked={z.ativo}
                  disabled={desabilitado}
                  onChange={(e) => onAlternar(z, e.target.checked)}
                />
                <strong>{z.palavra}</strong>
              </label>{' '}
              <span className="suave">
                {fmtSeg(z.inicio)} – {fmtSeg(z.inicio + z.duracao)} · clipe {z.clipe + 1}
              </span>
              {salvandoId === z.id && <span className="aviso-texto"> salvando…</span>}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

interface ItemProps {
  item: ItemImagem
  desabilitado: boolean
  salvando: boolean
  onEditar: (mudanca: ImagemEdit) => Promise<boolean>
}

function ItemCartao({ item, desabilitado, salvando, onEditar }: ItemProps) {
  const [query, setQuery] = useState(item.query)
  // a query salva pode mudar (nova busca ou plano recarregado)
  useEffect(() => setQuery(item.query), [item.query])

  const n = item.candidatos.length
  const cand = n > 0 ? item.candidatos[Math.min(item.escolhida, n - 1)] : null
  const q = query.trim()

  return (
    <div className={`imagem-item${item.ativa ? '' : ' inativa'}`}>
      <div className="imagem-miniatura">
        {cand ? <img src={cand.miniatura} alt={item.query} loading="lazy" /> : <span className="suave">sem foto</span>}
      </div>
      <div>
        <p>
          <strong>{item.palavra}</strong>{' '}
          <span className="suave">
            {fmtSeg(item.inicio)} – {fmtSeg(item.inicio + item.duracao)} · clipe {item.clipe + 1}
          </span>
        </p>
        <div className="linha">
          <button
            onClick={() => void onEditar({ escolhida: item.escolhida - 1 })}
            disabled={desabilitado || item.escolhida <= 0}
            title="foto anterior"
          >
            ◀
          </button>
          <span>{n > 0 ? `${item.escolhida + 1}/${n}` : '0/0'}</span>
          <button
            onClick={() => void onEditar({ escolhida: item.escolhida + 1 })}
            disabled={desabilitado || item.escolhida >= n - 1}
            title="próxima foto"
          >
            ▶
          </button>
          <label>
            <input
              type="checkbox"
              checked={item.ativa}
              disabled={desabilitado}
              onChange={(e) => void onEditar({ ativa: e.target.checked })}
            />
            usar
          </label>
          {salvando && <span className="aviso-texto">salvando…</span>}
        </div>
        <form
          className="linha"
          onSubmit={(ev) => {
            ev.preventDefault()
            if (q.length >= 2) void onEditar({ query: q })
          }}
        >
          <input value={query} onChange={(e) => setQuery(e.target.value)} maxLength={80} disabled={desabilitado} />
          <button type="submit" disabled={desabilitado || q.length < 2 || q === item.query}>
            buscar de novo
          </button>
        </form>
        {cand && (
          <p className="suave">
            foto:{' '}
            <a href={cand.pagina || cand.url} target="_blank" rel="noreferrer">
              {cand.autor || 'autor desconhecido'} ({cand.fonte})
            </a>
          </p>
        )}
      </div>
    </div>
  )
}
