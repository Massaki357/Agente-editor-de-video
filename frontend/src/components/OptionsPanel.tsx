import { useId, useState, type ChangeEvent } from 'react'
import { api, mensagemErro, type ConfigOut, type JobTipo, type PipelineOptions, type ProjectOut, type TransitionId } from '../api'

interface Props {
  /** GET /config: opções padrão e modelos de LLM (null enquanto não chegou) */
  config: ConfigOut | null
  projetoId: string
  /** Preferências de vídeo salvas no projeto. */
  opcoesProjeto?: Pick<ProjectOut,
    | 'estabilizar'
    | 'suavizacao_estabilizacao'
    | 'broll'
    | 'broll_intervalo_min'
    | 'broll_transition'
    | 'legendas_continuas'
    | 'legendas_destaque'
    | 'estilo_destaque'
  >
  bloqueado: boolean
  semClipes: boolean
  onRodar: (tipo: JobTipo, opcoes?: PipelineOptions) => void
  onTransicaoSalva: (preset: TransitionId) => void
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
  broll: false,
  broll_intervalo_min: 8,
  broll_transition: 'hard_cut',
  cortes: true,
  cortes_fala: true,
  reenquadrar: true,
  legendas_continuas: true,
  legendas_destaque: false,
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
  estilo_destaque: {
    fonte: 'Poppins',
    tamanho: 72,
    cor: '#FFFFFF',
    cor_entrada: '#FFD400',
    cor_contorno: '#000000',
    contorno: 5,
    sombra: 2,
    margem_lateral: 80,
    duracao_permanencia: 1.2,
    fade_saida: 0.25,
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

export default function OptionsPanel({ config, projetoId, opcoesProjeto, bloqueado, semClipes, onRodar, onTransicaoSalva }: Props) {
  const padrao = config?.opcoes_padrao ?? null
  const [op, setOp] = useState<PipelineOptions>(() => {
    const destaque = opcoesProjeto?.legendas_destaque ?? padrao?.legendas_destaque ?? FALLBACK.legendas_destaque
    const continua = opcoesProjeto?.legendas_continuas ?? padrao?.legendas_continuas ?? FALLBACK.legendas_continuas
    return {
      ...(padrao ?? FALLBACK),
      estabilizar: opcoesProjeto?.estabilizar ?? padrao?.estabilizar ?? FALLBACK.estabilizar,
      suavizacao_estabilizacao:
        opcoesProjeto?.suavizacao_estabilizacao ?? padrao?.suavizacao_estabilizacao ?? FALLBACK.suavizacao_estabilizacao,
      broll: opcoesProjeto?.broll ?? padrao?.broll ?? FALLBACK.broll,
      broll_intervalo_min: opcoesProjeto?.broll_intervalo_min ?? padrao?.broll_intervalo_min ?? FALLBACK.broll_intervalo_min,
      broll_transition: opcoesProjeto?.broll_transition ?? padrao?.broll_transition ?? FALLBACK.broll_transition,
      legendas_continuas: continua && !destaque,
      legendas_destaque: destaque,
      estilo_destaque: opcoesProjeto?.estilo_destaque ?? padrao?.estilo_destaque ?? FALLBACK.estilo_destaque,
    }
  })
  const id = useId()
  const [salvandoTransicao, setSalvandoTransicao] = useState(false)
  const [erroTransicao, setErroTransicao] = useState<string | null>(null)

  const salvarTransicaoPadrao = async (preset: TransitionId) => {
    const anterior = op.broll_transition
    if (preset === anterior) return
    setOp((atual) => ({ ...atual, broll_transition: preset }))
    setSalvandoTransicao(true)
    setErroTransicao(null)
    try {
      await api.setDefaultBrollTransition(projetoId, preset)
      onTransicaoSalva(preset)
    } catch (e) {
      setOp((atual) => ({ ...atual, broll_transition: anterior }))
      setErroTransicao(`Não foi possível salvar a transição padrão: ${mensagemErro(e)}`)
    } finally {
      setSalvandoTransicao(false)
    }
  }

  const setEstilo = (mudanca: Partial<PipelineOptions['estilo_legenda']>) =>
    setOp({ ...op, estilo_legenda: { ...op.estilo_legenda, ...mudanca } })
  const setEstiloDestaque = (mudanca: Partial<PipelineOptions['estilo_destaque']>) =>
    setOp({ ...op, estilo_destaque: { ...op.estilo_destaque, ...mudanca } })
  const num = (campo: 'min_silencio' | 'margem' | 'ruido_db') => (e: ChangeEvent<HTMLInputElement>) =>
    setOp({ ...op, [campo]: e.target.value === '' ? 0 : Number(e.target.value) })
  const setAudio = (mudanca: Partial<PipelineOptions['parametros_audio']>) =>
    setOp({ ...op, parametros_audio: { ...op.parametros_audio, ...mudanca } })

  const off = bloqueado || semClipes || salvandoTransicao
  const dica = bloqueado ? DICA_BLOQUEIO : semClipes ? DICA_SEM_CLIPES : undefined
  const modelos = config?.llm_models ?? []
  const modoLegenda = op.legendas_destaque ? 'destaque' : op.legendas_continuas ? 'continua' : 'nenhuma'

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

        <div className="campo">
          <label htmlFor={`${id}-modo-legenda`}>legendas</label>
          <select
            id={`${id}-modo-legenda`}
            value={modoLegenda}
            onChange={(e) => setOp({
              ...op,
              legendas_continuas: e.target.value === 'continua',
              legendas_destaque: e.target.value === 'destaque',
            })}
          >
            <option value="nenhuma">Nenhuma</option>
            <option value="continua">Legenda contínua</option>
            <option value="destaque">Legendas de destaque</option>
          </select>
        </div>
        {modoLegenda === 'continua' && (
          <details>
            <summary>estilo da legenda contínua</summary>
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
        {modoLegenda === 'destaque' && (
          <div className="recuada">
            <p className="suave">Frases de até 5 palavras, posicionadas para evitar rosto e imagens.</p>
            <div className="campo">
              <label htmlFor={`${id}-destaque-permanencia`}>permanência após a frase (s)</label>
              <input
                id={`${id}-destaque-permanencia`}
                type="number"
                min="0"
                max="5"
                step="0.1"
                value={op.estilo_destaque.duracao_permanencia}
                onChange={(e) => {
                  const v = Number(e.target.value)
                  setEstiloDestaque({ duracao_permanencia: e.target.value === '' || !Number.isFinite(v) ? 1.2 : Math.min(5, Math.max(0, v)) })
                }}
              />
            </div>
            <div className="campo">
              <label htmlFor={`${id}-destaque-tamanho`}>tamanho</label>
              <input
                id={`${id}-destaque-tamanho`}
                type="number"
                min="32"
                max="160"
                step="1"
                value={op.estilo_destaque.tamanho}
                onChange={(e) => {
                  const v = Number(e.target.value)
                  setEstiloDestaque({ tamanho: e.target.value === '' || !Number.isFinite(v) ? 72 : Math.min(160, Math.max(32, v)) })
                }}
              />
            </div>
            <div className="campo">
              <label htmlFor={`${id}-destaque-cor`}>cor do texto</label>
              <input
                id={`${id}-destaque-cor`}
                type="color"
                value={op.estilo_destaque.cor}
                onChange={(e) => setEstiloDestaque({ cor: e.target.value })}
              />
            </div>
            <div className="campo">
              <label htmlFor={`${id}-destaque-entrada`}>cor da palavra atual</label>
              <input
                id={`${id}-destaque-entrada`}
                type="color"
                value={op.estilo_destaque.cor_entrada}
                onChange={(e) => setEstiloDestaque({ cor_entrada: e.target.value })}
              />
            </div>
          </div>
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

        <label className="caixa">
          <input type="checkbox" checked={op.broll} onChange={(e) => setOp({ ...op, broll: e.target.checked })} />
          vídeos de apoio durante a fala (B-roll)
        </label>
        {op.broll && (
          <div className="recuada">
            <div className="campo">
              <label htmlFor={`${id}-broll-intervalo`}>intervalo mínimo entre vídeos (s)</label>
              <input
                id={`${id}-broll-intervalo`}
                type="number"
                min="8"
                max="30"
                step="1"
                value={op.broll_intervalo_min}
                onChange={(e) => {
                  const n = Number(e.target.value)
                  setOp({ ...op, broll_intervalo_min: Number.isFinite(n) ? Math.min(30, Math.max(8, n)) : 8 })
                }}
              />
            </div>
            <div className="campo">
              <label htmlFor={`${id}-broll-transition`}>transição padrão</label>
              <select
                id={`${id}-broll-transition`}
                value={op.broll_transition}
                disabled={salvandoTransicao}
                onChange={(e) => void salvarTransicaoPadrao(e.target.value as TransitionId)}
              >
                <option value="hard_cut">corte seco</option>
                <option value="crossfade">fusão</option>
                <option value="slide">deslizamento</option>
                <option value="wipe">varredura</option>
                <option value="reveal">revelação</option>
                <option value="zoom">zoom de entrada</option>
                <option value="blur">desfoque</option>
              </select>
              {salvandoTransicao && <span className="suave" role="status">salvando…</span>}
            </div>
          </div>
        )}
        {erroTransicao && <p className="texto-aviso" role="alert">{erroTransicao}</p>}

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
        <button
          type="button"
          onClick={() => onRodar('broll', op)}
          disabled={off || !op.broll}
          title={dica ?? (op.broll ? 'prepara os vídeos para revisão, sem render' : 'ative B-roll nas opções')}
        >
          Preparar B-roll
        </button>
        <button type="button" className="primario" onClick={() => onRodar('gerar', op)} disabled={off} title={dica}>
          ▶ Gerar vídeo
        </button>
        {padrao && (
          <button type="button" className="link" onClick={() => {
            setOp(padrao)
            void salvarTransicaoPadrao(padrao.broll_transition)
          }} disabled={bloqueado || salvandoTransicao}>
            restaurar padrões
          </button>
        )}
      </div>
    </div>
  )
}
