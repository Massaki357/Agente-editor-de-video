// Estado do plano criativo (imagens e zooms) de um projeto: carga, edição e erros.
// Fica fora do componente para o Workspace saber se existe plano (aba do palco).

import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, mensagemErro, type ImagemEdit, type ItemImagem, type ItemZoom, type PlanoOut } from './api'

export interface EstadoPlano {
  dados: PlanoOut | null
  semPlano: boolean
  carregando: boolean
  erro: string | null
  /** id do item/zoom sendo salvo (mostra "salvando…") */
  salvandoItem: number | null
  salvandoZoom: number | null
  recarregar: () => void
  limparErro: () => void
  editar: (item: ItemImagem, mudanca: ImagemEdit) => void
  alternarZoom: (zoom: ItemZoom, ativo: boolean) => void
}

/** `versao` muda quando um job 'imagens' ou 'gerar' termina: recarrega o plano. */
export function usePlano(projetoId: string, versao: number): EstadoPlano {
  const [dados, setDados] = useState<PlanoOut | null>(null)
  const [semPlano, setSemPlano] = useState(false)
  const [carregando, setCarregando] = useState(true)
  const [erro, setErro] = useState<string | null>(null)
  const [salvandoItem, setSalvandoItem] = useState<number | null>(null)
  const [salvandoZoom, setSalvandoZoom] = useState<number | null>(null)
  const [recarga, setRecarga] = useState(0)

  useEffect(() => {
    let vivo = true
    setCarregando(true)
    setErro(null)
    api
      .imagens(projetoId)
      .then((d) => {
        if (!vivo) return
        setDados(d)
        setSemPlano(false)
      })
      .catch((e) => {
        if (!vivo) return
        if (e instanceof ApiError && e.status === 404) {
          setDados(null)
          setSemPlano(true)
        } else {
          setErro(mensagemErro(e))
        }
      })
      .finally(() => vivo && setCarregando(false))
    return () => {
      vivo = false
    }
  }, [projetoId, versao, recarga])

  const editar = useCallback(
    (item: ItemImagem, mudanca: ImagemEdit) => {
      setSalvandoItem(item.id)
      setErro(null)
      api
        .editImagem(projetoId, item.id, mudanca)
        .then(setDados)
        .catch((e) => setErro(`imagem "${item.palavra}": ${mensagemErro(e)}`))
        .finally(() => setSalvandoItem(null))
    },
    [projetoId],
  )

  const alternarZoom = useCallback(
    (zoom: ItemZoom, ativo: boolean) => {
      setSalvandoZoom(zoom.id)
      setErro(null)
      api
        .setZoomActive(projetoId, zoom.id, ativo)
        .then(setDados)
        .catch((e) => setErro(`zoom "${zoom.palavra}": ${mensagemErro(e)}`))
        .finally(() => setSalvandoZoom(null))
    },
    [projetoId],
  )

  return {
    dados,
    semPlano,
    carregando,
    erro,
    salvandoItem,
    salvandoZoom,
    recarregar: useCallback(() => setRecarga((r) => r + 1), []),
    limparErro: useCallback(() => setErro(null), []),
    editar,
    alternarZoom,
  }
}
