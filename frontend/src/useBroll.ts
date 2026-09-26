// Estado da prévia de cutaways de um projeto: carga, aprovação e troca de busca.
import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, mensagemErro, type BrollEdit, type BrollItemOut, type BrollPreviewOut, type TransitionCatalogOut } from './api'

export interface EstadoBroll {
  dados: BrollPreviewOut | null
  catalogo: TransitionCatalogOut | null
  erroCatalogo: string | null
  carregandoCatalogo: boolean
  semPrevia: boolean
  carregando: boolean
  erro: string | null
  salvandoItem: number | null
  recarregar: () => void
  limparErro: () => void
  editar: (item: BrollItemOut, mudanca: BrollEdit) => void
}

/** `versao` muda quando um job de B-roll ou geração termina. */
export function useBroll(projetoId: string, versao: number): EstadoBroll {
  const [dados, setDados] = useState<BrollPreviewOut | null>(null)
  const [catalogo, setCatalogo] = useState<TransitionCatalogOut | null>(null)
  const [erroCatalogo, setErroCatalogo] = useState<string | null>(null)
  const [carregandoCatalogo, setCarregandoCatalogo] = useState(true)
  const [semPrevia, setSemPrevia] = useState(false)
  const [carregando, setCarregando] = useState(true)
  const [erro, setErro] = useState<string | null>(null)
  const [salvandoItem, setSalvandoItem] = useState<number | null>(null)
  const [recarga, setRecarga] = useState(0)

  useEffect(() => {
    let vivo = true
    setCarregandoCatalogo(true)
    setErroCatalogo(null)
    api.brollTransitions(projetoId)
      .then((d) => { if (vivo) setCatalogo(d) })
      .catch((e) => { if (vivo) setErroCatalogo(mensagemErro(e)) })
      .finally(() => { if (vivo) setCarregandoCatalogo(false) })
    return () => { vivo = false }
  }, [projetoId])

  useEffect(() => {
    let vivo = true
    setCarregando(true)
    setErro(null)
    api.broll(projetoId)
      .then((d) => {
        if (!vivo) return
        setDados(d)
        setSemPrevia(false)
      })
      .catch((e) => {
        if (!vivo) return
        if (e instanceof ApiError && e.status === 404) {
          setDados(null)
          setSemPrevia(true)
        } else {
          setErro(mensagemErro(e))
        }
      })
      .finally(() => { if (vivo) setCarregando(false) })
    return () => { vivo = false }
  }, [projetoId, versao, recarga])

  const editar = useCallback((item: BrollItemOut, mudanca: BrollEdit) => {
    setSalvandoItem(item.id)
    setErro(null)
    api.editBroll(projetoId, item.id, mudanca)
      .then(setDados)
      .catch((e) => setErro(`cutaway "${item.texto}": ${mensagemErro(e)}`))
      .finally(() => setSalvandoItem(null))
  }, [projetoId])

  return {
    dados,
    catalogo,
    erroCatalogo,
    carregandoCatalogo,
    semPrevia,
    carregando,
    erro,
    salvandoItem,
    recarregar: useCallback(() => setRecarga((r) => r + 1), []),
    limparErro: useCallback(() => setErro(null), []),
    editar,
  }
}
