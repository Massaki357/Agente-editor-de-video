import { useState, type ChangeEvent } from 'react'
import type { JobTipo, PipelineOptions } from '../api'

interface Props {
  padrao: PipelineOptions | null
  bloqueado: boolean
  semClipes: boolean
  onRodar: (tipo: JobTipo, opcoes?: PipelineOptions) => void
}

const FALLBACK: PipelineOptions = {
  cortes: true,
  cortes_fala: true,
  reenquadrar: true,
  legendas: true,
  estilo_legenda: {
    fonte: 'Poppins',
    tamanho: 84,
    cor: '#FFFFFF',
    cor_destaque: '#FFD400',
    cor_contorno: '#000000',
    contorno: 7,
    sombra: 3,
    margem_inferior: 520,
    margem_lateral: 70,
    maiusculas: true,
    palavras_max: 4,
    pausa_quebra: 0.45,
    destaque_escala: 112,
  },
  imagens: true,
  sticker: false,
  parametros_imagens: {
    intervalo_min: 3.0,
    duracao_min: 1.2,
    duracao_max: 3.0,
    antecedencia: 0.2,
    candidatos: 5,
  },
  zooms: true,
  parametros_zoom: { escala: 1.15, margem_rosto: 0.35, altura_rosto: 0.4 },
  min_silencio: 0.4,
  margem: 0.08,
  ruido_db: -35,
}

export default function Actions({ padrao, bloqueado, semClipes, onRodar }: Props) {
  // o pai recria o componente (key) quando os padrões chegam de GET /config
  const [op, setOp] = useState<PipelineOptions>(padrao ?? FALLBACK)

  const setEstilo = (mudanca: Partial<PipelineOptions['estilo_legenda']>) =>
    setOp({ ...op, estilo_legenda: { ...op.estilo_legenda, ...mudanca } })

  const off = bloqueado || semClipes
  const num = (campo: 'min_silencio' | 'margem' | 'ruido_db') => (e: ChangeEvent<HTMLInputElement>) =>
    setOp({ ...op, [campo]: e.target.value === '' ? 0 : Number(e.target.value) })

  return (
    <section className="cartao">
      <h3>Ações</h3>
      {semClipes && <p className="suave">Adicione clipes para liberar as ações.</p>}
      <div className="linha">
        <button onClick={() => onRodar('transcrever')} disabled={off}>
          Transcrever
        </button>
        <button onClick={() => onRodar('rosto')} disabled={off}>
          Rastrear rosto
        </button>
      </div>
      <fieldset disabled={off}>
        <legend>Gerar vídeo</legend>
        <label>
          <input type="checkbox" checked={op.cortes} onChange={(e) => setOp({ ...op, cortes: e.target.checked })} />
          cortar silêncios
        </label>
        <label>
          <input
            type="checkbox"
            checked={op.cortes_fala}
            onChange={(e) => setOp({ ...op, cortes_fala: e.target.checked })}
          />
          cortar erros de fala com LLM (gasta tokens)
        </label>
        <label>
          <input
            type="checkbox"
            checked={op.reenquadrar}
            onChange={(e) => setOp({ ...op, reenquadrar: e.target.checked })}
          />
          vertical 9:16 (1080x1920) seguindo o rosto
        </label>
        <label title={op.reenquadrar ? undefined : 'só funciona com o vertical 9:16 ligado'}>
          <input
            type="checkbox"
            checked={op.zooms && op.reenquadrar}
            disabled={!op.reenquadrar}
            onChange={(e) => setOp({ ...op, zooms: e.target.checked })}
          />
          zooms no rosto em momentos de ênfase (LLM)
        </label>
        {op.zooms && op.reenquadrar && (
          <label>
            escala do zoom
            <input
              type="number"
              min="1"
              max="1.6"
              step="0.05"
              value={op.parametros_zoom.escala}
              onChange={(e) => {
                const v = Number(e.target.value)
                const escala = e.target.value === '' || Number.isNaN(v) ? 1.15 : Math.min(1.6, Math.max(1, v))
                setOp({ ...op, parametros_zoom: { ...op.parametros_zoom, escala } })
              }}
            />
          </label>
        )}
        <label>
          <input type="checkbox" checked={op.legendas} onChange={(e) => setOp({ ...op, legendas: e.target.checked })} />
          legendas palavra por palavra
        </label>
        {op.legendas && (
          <div className="linha">
            <label>
              tamanho
              <input
                type="number"
                min="20"
                max="200"
                value={op.estilo_legenda.tamanho}
                onChange={(e) => setEstilo({ tamanho: Number(e.target.value) || 84 })}
              />
            </label>
            <label>
              cor
              <input type="color" value={op.estilo_legenda.cor} onChange={(e) => setEstilo({ cor: e.target.value })} />
            </label>
            <label>
              destaque
              <input
                type="color"
                value={op.estilo_legenda.cor_destaque}
                onChange={(e) => setEstilo({ cor_destaque: e.target.value })}
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={op.estilo_legenda.maiusculas}
                onChange={(e) => setEstilo({ maiusculas: e.target.checked })}
              />
              MAIÚSCULAS
            </label>
          </div>
        )}
        <label>
          <input type="checkbox" checked={op.imagens} onChange={(e) => setOp({ ...op, imagens: e.target.checked })} />
          imagens sobre a fala (LLM + Pexels)
        </label>
        {op.imagens && (
          <label>
            <input type="checkbox" checked={op.sticker} onChange={(e) => setOp({ ...op, sticker: e.target.checked })} />
            sticker (sem fundo)
          </label>
        )}
        <label>
          silêncio mínimo (s)
          <input type="number" step="0.05" min="0" value={op.min_silencio} onChange={num('min_silencio')} />
        </label>
        <label>
          margem (s)
          <input type="number" step="0.01" min="0" value={op.margem} onChange={num('margem')} />
        </label>
        <label>
          ruído (dB)
          <input type="number" step="1" max="0" value={op.ruido_db} onChange={num('ruido_db')} />
        </label>
        <div className="linha">
          <button className="primario" onClick={() => onRodar('gerar', op)}>
            Gerar vídeo
          </button>
          <button onClick={() => onRodar('imagens', op)} title="aplica os cortes e monta o plano criativo (imagens e zooms), sem render">
            Sugerir imagens e zooms (preview)
          </button>
          {padrao && (
            <button className="link" onClick={() => setOp(padrao)}>
              restaurar padrões
            </button>
          )}
        </div>
      </fieldset>
    </section>
  )
}
