import { useEffect, useState } from 'react'
import { fmtSeg, type ImagemEdit, type ItemImagem, type ItemZoom } from '../api'
import type { EstadoPlano } from '../usePlano'
import type { EstadoBroll } from '../useBroll'
import BrollList from './BrollList'
import ErrorBox from './ErrorBox'

interface Props {
  plano: EstadoPlano
  broll: EstadoBroll
  /** job ativo ou alteração em andamento: trava as edições */
  bloqueado: boolean
}

/** Palco "Plano criativo": imagens sugeridas pelo LLM e zooms no rosto. */
export default function ImagePlan({ plano, broll, bloqueado }: Props) {
  const itens = plano.dados?.plano.itens ?? []
  const zooms = plano.dados?.plano.zooms ?? []
  const ativas = itens.filter((i) => i.ativa && i.candidatos.length > 0).length
  const zoomsAtivos = zooms.filter((z) => z.ativo).length
  const ocupado = bloqueado || plano.salvandoItem !== null || plano.salvandoZoom !== null

  return (
    <div className="plano">
      <div className="linha entre">
        <p className="suave">
          Trocar fotos ou ligar/desligar zooms aqui não chama o LLM de novo; depois clique em Gerar vídeo.
        </p>
        <button type="button" className="pequeno" onClick={plano.recarregar} disabled={plano.carregando}>
          recarregar
        </button>
      </div>

      <div aria-live="polite">
        <ErrorBox erro={plano.erro} onClose={plano.limparErro} />
      </div>

      {plano.carregando && !plano.dados && <p className="suave">carregando plano…</p>}
      {plano.semPlano && (
        <p className="suave">
          Nenhum plano criativo ainda. Use "Sugerir imagens e zooms" ou "Gerar vídeo".
        </p>
      )}
      {plano.dados && !plano.dados.valido && (
        <p className="texto-aviso">Os cortes mudaram; o próximo "Gerar vídeo" refaz o plano.</p>
      )}

      {plano.dados && (
        <>
          <h3 className="secao">
            Imagens <span className="suave">{itens.length} item(ns), {ativas} em uso</span>
          </h3>
          {itens.length === 0 && <p className="suave">O LLM não sugeriu nenhuma imagem.</p>}
          <div className="imagens">
            {itens.map((item) => (
              <ItemCartao
                key={item.id}
                item={item}
                desabilitado={ocupado}
                salvando={plano.salvandoItem === item.id}
                onEditar={(m) => plano.editar(item, m)}
              />
            ))}
          </div>

          <ZoomLista
            zooms={zooms}
            ativos={zoomsAtivos}
            desabilitado={ocupado}
            salvandoId={plano.salvandoZoom}
            onAlternar={plano.alternarZoom}
          />
        </>
      )}
      <BrollList broll={broll} bloqueado={bloqueado} />
    </div>
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
      <h3 className="secao">
        Zooms no rosto <span className="suave">{zooms.length} zoom(s), {ativos} em uso</span>
      </h3>
      <p className="suave">Só entram no vídeo com o vertical 9:16 e "zooms no rosto" ligados.</p>
      {zooms.length === 0 ? (
        <p className="suave">O LLM não sugeriu nenhum zoom.</p>
      ) : (
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
              </label>
              <span className="suave numeros">
                {fmtSeg(z.inicio)} – {fmtSeg(z.inicio + z.duracao)} · clipe {z.clipe + 1}
              </span>
              {salvandoId === z.id && <span className="texto-aviso">salvando…</span>}
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
  onEditar: (mudanca: ImagemEdit) => void
}

function ItemCartao({ item, desabilitado, salvando, onEditar }: ItemProps) {
  const [query, setQuery] = useState(item.query)
  // a query salva pode mudar (nova busca ou plano recarregado)
  useEffect(() => setQuery(item.query), [item.query])

  const n = item.candidatos.length
  const cand = n > 0 ? item.candidatos[Math.min(item.escolhida, n - 1)] : null
  const q = query.trim()
  const idBusca = `busca-${item.id}`

  return (
    <div className={`imagem-item${item.ativa ? '' : ' inativa'}`}>
      <div className="imagem-miniatura">
        {cand ? <img src={cand.miniatura} alt={item.query} loading="lazy" /> : <span className="suave">sem foto</span>}
      </div>
      <div className="imagem-corpo">
        <p>
          <strong>{item.palavra}</strong>{' '}
          <span className="suave numeros">
            {fmtSeg(item.inicio)} – {fmtSeg(item.inicio + item.duracao)} · clipe {item.clipe + 1}
          </span>
        </p>
        <div className="linha">
          <button
            type="button"
            className="icone"
            onClick={() => onEditar({ escolhida: item.escolhida - 1 })}
            disabled={desabilitado || item.escolhida <= 0}
            title="Foto anterior"
          >
            ◀<span className="oculto">Foto anterior</span>
          </button>
          <span className="numeros">{n > 0 ? `${item.escolhida + 1}/${n}` : '0/0'}</span>
          <button
            type="button"
            className="icone"
            onClick={() => onEditar({ escolhida: item.escolhida + 1 })}
            disabled={desabilitado || item.escolhida >= n - 1}
            title="Próxima foto"
          >
            ▶<span className="oculto">Próxima foto</span>
          </button>
          <label className="caixa">
            <input
              type="checkbox"
              checked={item.ativa}
              disabled={desabilitado}
              onChange={(e) => onEditar({ ativa: e.target.checked })}
            />
            usar
          </label>
          {salvando && <span className="texto-aviso">salvando…</span>}
        </div>
        <form
          className="linha"
          onSubmit={(ev) => {
            ev.preventDefault()
            if (q.length >= 2) onEditar({ query: q })
          }}
        >
          <label className="oculto" htmlFor={idBusca}>
            Busca da imagem de "{item.palavra}"
          </label>
          <input
            id={idBusca}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            maxLength={80}
            disabled={desabilitado}
          />
          <button type="submit" className="pequeno" disabled={desabilitado || q.length < 2 || q === item.query}>
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
