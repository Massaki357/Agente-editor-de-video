import { useId, useState, type ChangeEvent } from 'react'
import type { ConfigOut, JobTipo, PipelineOptions, ProjectOut } from '../api'

interface Props {
  /** GET /config: opções padrão e modelos de LLM (null enquanto não chegou) */
  config: ConfigOut | null
  /** Preferências de estabilização salvas no projeto. */
  opcoesProjeto?: Pick<ProjectOut, 'estabilizar' | 'suavizacao_estabilizacao'>
  bloqueado: boolean
  semClipes: boolean
  onRodar: (tipo: JobTipo, opcoes?: PipelineOptions) => void
}

/** Usado só enquanto GET /config não responde (o pai recria o painel com a key). */
const FALLBACK: PipelineOptions = {
  limpar_audio: false,
  parametros_audio: {
    aggressiveness: 0.5,
    alvo_lufs: -16,
    true_peak: -1.5,
    highpass_hz: 80,
    normalizar: true,
    motor: null,
  },
  estabilizar: false,
  suavizacao_estabilizacao: 'medio',
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
  llm_model: null,
  zooms: true,
  parametros_zoom: { escala: 1.15, margem_rosto: 0.35, altura_rosto: 0.4 },
  min_silencio: 0.4,
  margem: 0.08,
  ruido_db: -35,
}

const DICA_BLOQUEIO = 'há um job em andamento: espere terminar ou cancele'
const DICA_SEM_CLIPES = 'adicione clipes ao projeto primeiro'

export default function OptionsPanel({ config, opcoesProjeto, bloqueado, semClipes, onRodar }: Props) {
  const padrao = config?.opcoes_padrao ?? null
  const [op, setOp] = useState<PipelineOptions>(() => ({
    ...(padrao ?? FALLBACK),
    estabilizar: opcoesProjeto?.estabilizar ?? padrao?.estabilizar ?? FALLBACK.estabilizar,
    suavizacao_estabilizacao:
      opcoesProjeto?.suavizacao_estabilizacao ?? padrao?.suavizacao_estabilizacao ?? FALLBACK.suavizacao_estabilizacao,
  }))
  const id = useId()

  const setEstilo = (mudanca: Partial<PipelineOptions['estilo_legenda']>) =>
    setOp({ ...op, estilo_legenda: { ...op.estilo_legenda, ...mudanca } })
  const num = (campo: 'min_silencio' | 'margem' | 'ruido_db') => (e: ChangeEvent<HTMLInputElement>) =>
    setOp({ ...op, [campo]: e.target.value === '' ? 0 : Number(e.target.value) })
  const setAudio = (mudanca: Partial<PipelineOptions['parametros_audio']>) =>
    setOp({ ...op, parametros_audio: { ...op.parametros_audio, ...mudanca } })

  const off = bloqueado || semClipes
  const dica = bloqueado ? DICA_BLOQUEIO : semClipes ? DICA_SEM_CLIPES : undefined
  const modelos = config?.llm_models ?? []

  return (
    <div className="opcoes">
      <h2 className="secao">Opções</h2>

      {dica && <p className="texto-aviso">{bloqueado ? 'Job em andamento: opções e ações travadas.' : 'Adicione clipes para liberar as ações.'}</p>}

      <fieldset className="grupo" disabled={off} title={dica}>
        <legend>Áudio</legend>
        <label className="caixa">
          <input
            type="checkbox"
            checked={op.limpar_audio}
            onChange={(e) => setOp({ ...op, limpar_audio: e.target.checked })}
          />
          limpar o ruído do áudio do vídeo
        </label>
        <p className="suave">Vale para o áudio do vídeo final; a transcrição continua usando o áudio original.</p>
        {op.limpar_audio && (
          <details>
            <summary>ajustes do áudio</summary>
            <div className="campo">
              <label htmlFor={`${id}-agressividade`}>intensidade da limpeza</label>
              <input
                id={`${id}-agressividade`}
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={op.parametros_audio.aggressiveness}
                onChange={(e) => {
                  const v = Number(e.target.value)
                  const aggressiveness = e.target.value === '' || Number.isNaN(v) ? 0.5 : Math.min(1, Math.max(0, v))
                  setAudio({ aggressiveness })
                }}
              />
            </div>
            <label className="caixa">
              <input
                type="checkbox"
                checked={op.parametros_audio.normalizar}
                onChange={(e) => setAudio({ normalizar: e.target.checked })}
              />
              ajustar o volume
            </label>
            {op.parametros_audio.normalizar && (
              <div className="campo recuada">
                <label htmlFor={`${id}-lufs`}>volume alvo (LUFS)</label>
                <input
                  id={`${id}-lufs`}
                  type="number"
                  max="0"
                  step="1"
                  value={op.parametros_audio.alvo_lufs}
                  onChange={(e) => {
                    const v = Number(e.target.value)
                    const alvo_lufs = e.target.value === '' || Number.isNaN(v) ? -16 : Math.min(0, v)
                    setAudio({ alvo_lufs })
                  }}
                />
              </div>
            )}
          </details>
        )}
      </fieldset>

      <fieldset className="grupo" disabled={off} title={dica}>
        <legend>Cortes</legend>
        <label className="caixa">
          <input type="checkbox" checked={op.cortes} onChange={(e) => setOp({ ...op, cortes: e.target.checked })} />
          silêncios
        </label>
        <label className="caixa">
          <input
            type="checkbox"
            checked={op.cortes_fala}
            onChange={(e) => setOp({ ...op, cortes_fala: e.target.checked })}
          />
          erros de fala (LLM)
        </label>
        <div className="campo">
          <label htmlFor={`${id}-llm`}>modelo do LLM</label>
          <select
            id={`${id}-llm`}
            value={op.llm_model ?? ''}
            onChange={(e) => setOp({ ...op, llm_model: e.target.value || null })}
          >
            <option value="">padrão ({config?.llm_model ?? 'do .env'})</option>
            {modelos.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <details>
          <summary>detecção de silêncio</summary>
          <div className="campo">
            <label htmlFor={`${id}-sil`}>silêncio mínimo (s)</label>
            <input
              id={`${id}-sil`}
              type="number"
              step="0.05"
              min="0"
              value={op.min_silencio}
              onChange={num('min_silencio')}
            />
          </div>
          <div className="campo">
            <label htmlFor={`${id}-margem`}>margem (s)</label>
            <input id={`${id}-margem`} type="number" step="0.01" min="0" value={op.margem} onChange={num('margem')} />
          </div>
          <div className="campo">
            <label htmlFor={`${id}-ruido`}>ruído (dB)</label>
            <input id={`${id}-ruido`} type="number" step="1" max="0" value={op.ruido_db} onChange={num('ruido_db')} />
          </div>
        </details>
      </fieldset>

      <fieldset className="grupo" disabled={off} title={dica}>
        <legend>Imagem</legend>
        <label className="caixa">
          <input
            type="checkbox"
            checked={op.estabilizar}
            onChange={(e) => setOp({ ...op, estabilizar: e.target.checked })}
          />
          estabilizar clipes tremidos
        </label>
        {op.estabilizar && (
          <div className="campo recuada">
            <label htmlFor={`${id}-estabilizacao`}>suavização</label>
            <select
              id={`${id}-estabilizacao`}
              value={op.suavizacao_estabilizacao}
              onChange={(e) => setOp({
                ...op,
                suavizacao_estabilizacao: e.target.value as PipelineOptions['suavizacao_estabilizacao'],
              })}
            >
              <option value="leve">leve</option>
              <option value="medio">média</option>
              <option value="forte">forte</option>
            </select>
          </div>
        )}
        <p className="suave">Aplicada antes do rastreio de rosto e do reenquadramento.</p>
        <label className="caixa">
          <input
            type="checkbox"
            checked={op.reenquadrar}
            onChange={(e) => setOp({ ...op, reenquadrar: e.target.checked })}
          />
          vertical 9:16 seguindo o rosto
        </label>

        <label className="caixa">
          <input type="checkbox" checked={op.legendas} onChange={(e) => setOp({ ...op, legendas: e.target.checked })} />
          legendas palavra por palavra
        </label>
        {op.legendas && (
          <details>
            <summary>estilo da legenda</summary>
            <div className="campo">
              <label htmlFor={`${id}-tam`}>tamanho</label>
              <input
                id={`${id}-tam`}
                type="number"
                min="20"
                max="200"
                value={op.estilo_legenda.tamanho}
                onChange={(e) => setEstilo({ tamanho: Number(e.target.value) || 84 })}
              />
            </div>
            <div className="campo">
              <label htmlFor={`${id}-cor`}>cor</label>
              <input
                id={`${id}-cor`}
                type="color"
                value={op.estilo_legenda.cor}
                onChange={(e) => setEstilo({ cor: e.target.value })}
              />
            </div>
            <div className="campo">
              <label htmlFor={`${id}-destaque`}>destaque</label>
              <input
                id={`${id}-destaque`}
                type="color"
                value={op.estilo_legenda.cor_destaque}
                onChange={(e) => setEstilo({ cor_destaque: e.target.value })}
              />
            </div>
            <label className="caixa">
              <input
                type="checkbox"
                checked={op.estilo_legenda.maiusculas}
                onChange={(e) => setEstilo({ maiusculas: e.target.checked })}
              />
              MAIÚSCULAS
            </label>
          </details>
        )}

        <label className="caixa">
          <input type="checkbox" checked={op.imagens} onChange={(e) => setOp({ ...op, imagens: e.target.checked })} />
          imagens sobre a fala (LLM + Pexels)
        </label>
        {op.imagens && (
          <label className="caixa recuada">
            <input type="checkbox" checked={op.sticker} onChange={(e) => setOp({ ...op, sticker: e.target.checked })} />
            sticker (sem fundo)
          </label>
        )}

        <label className="caixa" title={op.reenquadrar ? undefined : 'só funciona com o vertical 9:16 ligado'}>
          <input
            type="checkbox"
            checked={op.zooms && op.reenquadrar}
            disabled={!op.reenquadrar}
            onChange={(e) => setOp({ ...op, zooms: e.target.checked })}
          />
          zooms no rosto (LLM)
        </label>
        {op.zooms && op.reenquadrar && (
          <div className="campo recuada">
            <label htmlFor={`${id}-escala`}>escala do zoom</label>
            <input
              id={`${id}-escala`}
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
          </div>
        )}
      </fieldset>

      <fieldset className="grupo" disabled={off} title={dica}>
        <legend>Preparo dos clipes</legend>
        <div className="linha">
          <button type="button" onClick={() => onRodar('transcrever')} title={dica}>
            Transcrever
          </button>
          <button type="button" onClick={() => onRodar('rosto', op)} title={dica}>
            Rastrear rosto
          </button>
        </div>
      </fieldset>

      <div className="acoes-principais" title={dica}>
        <button
          type="button"
          onClick={() => onRodar('imagens', op)}
          disabled={off}
          title={dica ?? 'aplica os cortes e monta o plano criativo (imagens e zooms), sem render'}
        >
          Sugerir imagens e zooms
        </button>
        <button type="button" className="primario" onClick={() => onRodar('gerar', op)} disabled={off} title={dica}>
          ▶ Gerar vídeo
        </button>
        {padrao && (
          <button type="button" className="link" onClick={() => setOp(padrao)} disabled={bloqueado}>
            restaurar padrões
          </button>
        )}
      </div>
    </div>
  )
}
