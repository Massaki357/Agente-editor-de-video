import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, fmtSeg, mensagemErro, type ImagemEdit, type ItemImagem, type PlanoOut } from '../api'
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

  const itens = dados?.plano.itens ?? []
  const ativas = itens.filter((i) => i.ativa && i.candidatos.length > 0).length

  return (
    <section className="cartao">
      <div className="linha">
        <h3>Imagens (preview)</h3>
        <button className="link" onClick={() => void carregar()} disabled={carregando}>
          recarregar
        </button>
      </div>
      <p className="suave">
        Trocar fotos aqui não chama o LLM de novo; depois clique em Gerar vídeo.
      </p>
      <ErrorBox erro={erro} onClose={() => setErro(null)} />
      {carregando && !dados && <p className="suave">carregando plano…</p>}
      {semPlano && (
        <p className="suave">Nenhum plano de imagens ainda. Use "Sugerir imagens (preview)" ou "Gerar vídeo".</p>
      )}
      {dados && !dados.valido && (
        <p className="aviso-texto">Os cortes mudaram; o próximo "Gerar vídeo" refaz o plano.</p>
      )}
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
            desabilitado={bloqueado || salvando !== null}
            salvando={salvando === item.id}
            onEditar={(m) => editar(item, m)}
          />
        ))}
      </div>
    </section>
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
