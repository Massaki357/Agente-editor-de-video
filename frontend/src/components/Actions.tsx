import { useState, type ChangeEvent } from 'react'
import type { JobTipo, PipelineOptions } from '../api'

interface Props {
  padrao: PipelineOptions | null
  bloqueado: boolean
  semClipes: boolean
  onRodar: (tipo: JobTipo, opcoes?: PipelineOptions) => void
}

const FALLBACK: PipelineOptions = { cortes: true, cortes_fala: true, min_silencio: 0.4, margem: 0.08, ruido_db: -35 }

export default function Actions({ padrao, bloqueado, semClipes, onRodar }: Props) {
  // o pai recria o componente (key) quando os padrões chegam de GET /config
  const [op, setOp] = useState<PipelineOptions>(padrao ?? FALLBACK)

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
