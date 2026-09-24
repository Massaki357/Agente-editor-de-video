// Cliente tipado da API do editor de vídeos. Espelha API/src/api/schemas.py e API/src/api/jobs.py.
// Todas as chamadas do frontend passam por aqui; os componentes não usam fetch direto.

const BASE = '/api'

// ----------------------------------------------------------------------------- tipos

/** Espelha `CaptionStyle` (API/src/captions.py). Cores em #RRGGBB. */
export interface CaptionStyle {
  fonte: string
  tamanho: number
  cor: string
  cor_destaque: string
  cor_contorno: string
  contorno: number
  sombra: number
  margem_inferior: number
  margem_lateral: number
  maiusculas: boolean
  palavras_max: number
  pausa_quebra: number
  destaque_escala: number
}

/** Espelha `HighlightStyle` (API/src/highlight_captions/ass_builder.py). */
export interface HighlightStyle {
  fonte: string
  tamanho: number
  cor: string
  cor_entrada: string
  cor_contorno: string
  contorno: number
  sombra: number
  margem_lateral: number
  duracao_permanencia: number
  fade_saida: number
}

/** Espelha `ImageParams` (API/src/images.py). */
export interface ImageParams {
  intervalo_min: number
  duracao_min: number
  duracao_max: number
  antecedencia: number
  candidatos: number
}

/** Espelha `ZoomParams` (API/src/reframe.py). */
export interface ZoomParams {
  /** zoom máximo, 1.0–1.6 (1.15 = 15% mais perto) */
  escala: number
  margem_rosto: number
  altura_rosto: number
}

/** Motores de redução de ruído (espelha `Motor` em API/src/audio/denoise.py). */
export type AudioMotor = 'deepfilternet' | 'noisereduce' | 'afftdn' | 'nenhum'

/** Espelha `AudioParams` (API/src/audio/optimize.py). */
export interface AudioParams {
  /** 0 não limpa, 1 limpa ao máximo */
  aggressiveness: number
  /** volume alvo em LUFS (≤ 0; -16 é o padrão das redes sociais) */
  alvo_lufs: number
  /** pico real máximo em dBTP (≤ 0) */
  true_peak: number
  /** corta abaixo disso, 0–300 Hz (0 desliga) */
  highpass_hz: number
  /** aplicar o loudnorm no fim */
  normalizar: boolean
  /** forçar um motor de denoise; `null` = o melhor disponível */
  motor: AudioMotor | null
}

export interface PipelineOptions {
  /** limpa o ruído do **áudio do vídeo final**; a transcrição usa o áudio original */
  limpar_audio: boolean
  parametros_audio: AudioParams
  /** estabiliza cada clipe antes de rastrear o rosto e reenquadrar */
  estabilizar: boolean
  suavizacao_estabilizacao: 'leve' | 'medio' | 'forte'
  broll: boolean
  /** Intervalo mínimo entre o início de dois cutaways, em segundos. */
  broll_intervalo_min: number
  broll_transition: 'hard_cut' | 'crossfade' | 'slide' | 'wipe'
  cortes: boolean
  cortes_fala: boolean
  reenquadrar: boolean
  legendas_continuas: boolean
  legendas_destaque: boolean
  estilo_legenda: CaptionStyle
  estilo_destaque: HighlightStyle
  imagens: boolean
  sticker: boolean
  parametros_imagens: ImageParams
  /** modelo do LLM desta execução; `null` = o do `.env` (ver `ConfigOut.llm_model`) */
  llm_model: string | null
  /** zooms no rosto em momentos de ênfase; só tem efeito com `reenquadrar` */
  zooms: boolean
  parametros_zoom: ZoomParams
  min_silencio: number
  margem: number
  ruido_db: number
}

export interface ClipOut {
  indice: number
  nome: string
  arquivo: string
  duracao: number | null
  largura: number | null
  altura: number | null
  fps: number | null
  tem_audio: boolean | null
  /** fps variável: o enquadramento pode ficar alguns frames defasado */
  vfr: boolean
  /** HDR: convertido para SDR (BT.709) no render */
  hdr: boolean
  trechos: [number, number][]
  offset: number
  duracao_mantida: number
  transcrito: boolean
  rosto: boolean
  video_url: string
  thumbnail_url: string
}

export interface ProjectSummary {
  id: string
  nome: string
  criado: string
  atualizado: string
  n_clipes: number
  video_final_url: string | null
}

export interface ProjectOut extends ProjectSummary {
  clipes: ClipOut[]
  duracao_total: number
  arquivos: string[]
  job_ativo: string | null
  estabilizar: boolean
  suavizacao_estabilizacao: PipelineOptions['suavizacao_estabilizacao']
  broll: boolean
  broll_intervalo_min: number
  broll_transition: PipelineOptions['broll_transition']
  legendas_continuas: boolean
  legendas_destaque: boolean
  estilo_destaque: HighlightStyle
}

export interface BrollItemOut {
  id: number
  texto: string
  query: string
  inicio: number
  duracao: number
  ativo: boolean
  aprovado: boolean
  fonte: string | null
  autor: string | null
  pagina: string | null
  video_url: string | null
}

export interface BrollPreviewOut {
  valido: boolean
  itens: BrollItemOut[]
}

export interface BrollEdit {
  query?: string
  ativo?: boolean
  aprovado?: boolean
}

export interface Palavra {
  indice: number
  texto: string
  inicio: number
  fim: number
  prob: number
}

export interface TranscricaoOut {
  modelo: string
  duracao: number
  texto: string
  palavras: Palavra[]
}

export interface RostoOut {
  fps: number
  largura: number
  altura: number
  n_frames: number
  cobertura: number
  debug_url: string | null
}

export type CheckStatus = 'OK' | 'AVISO' | 'FALTA'

export interface CheckOut {
  nome: string
  status: CheckStatus
  detalhe: string
}

export interface ConfigOut {
  /** modelo do `.env`: é o usado quando `PipelineOptions.llm_model` é nulo */
  llm_model: string
  /** modelos que podem ser escolhidos nas opções (provedores com chave) */
  llm_models: string[]
  whisper_model: string
  whisper_device: string
  saida: Record<string, number>
  opcoes_padrao: PipelineOptions
}

/** Espelha `Candidato` (API/src/images.py). */
export interface Candidato {
  fonte: 'pexels' | 'pixabay'
  id: string
  url: string
  miniatura: string
  autor: string
  pagina: string
}

/** Espelha `ItemImagem` (API/src/images.py). Tempos em segundos do vídeo final. */
export interface ItemImagem {
  id: number
  indice: number
  clipe: number
  palavra: string
  query: string
  inicio: number
  duracao: number
  candidatos: Candidato[]
  escolhida: number
  ativa: boolean
}

/** Espelha `ItemZoom` (API/src/images.py). Tempos em segundos do vídeo final. */
export interface ItemZoom {
  id: number
  indice: number
  clipe: number
  palavra: string
  inicio: number
  duracao: number
  ativo: boolean
}

/** Plano criativo: imagens + zooms no rosto. */
export interface PlanoImagens {
  assinatura: string
  itens: ItemImagem[]
  zooms: ItemZoom[]
}

/** Espelha `PlanoOut` (API/src/api/schemas.py). */
export interface PlanoOut {
  valido: boolean
  plano: PlanoImagens
}

/** Espelha `ImagemEdit`: só os campos enviados mudam. */
export interface ImagemEdit {
  escolhida?: number
  ativa?: boolean
  query?: string
}

/** Espelha `UsoLLM` (API/src/llm/pricing.py): uso do LLM na execução de um job.
 * `custo_usd` nulo = modelo fora da tabela de preços (mostre só os tokens). */
export interface UsoLLM {
  chamadas: number
  tokens_entrada: number
  tokens_saida: number
  custo_usd: number | null
  modelos: string[]
}

export type JobTipo = 'transcrever' | 'rosto' | 'imagens' | 'broll' | 'gerar'
export type JobStatus = 'pendente' | 'rodando' | 'concluido' | 'erro' | 'cancelado'

export interface Job {
  id: string
  projeto_id: string
  tipo: JobTipo | string
  opcoes: Record<string, unknown>
  status: JobStatus
  etapa: string | null
  progresso: number
  mensagem: string | null
  resultado: Record<string, unknown> | null
  log: string[]
  criado: string
  iniciado: string | null
  terminado: string | null
  cancelar: boolean
}

const NOMES_JOB: Record<string, string> = {
  transcrever: 'Transcrever',
  rosto: 'Rastrear rosto',
  imagens: 'Sugerir imagens e zooms',
  broll: 'Preparar B-roll',
  gerar: 'Gerar vídeo',
}

export function nomeJob(tipo: string): string {
  return NOMES_JOB[tipo] ?? tipo
}

export function jobAtivo(job: Job): boolean {
  return job.status === 'pendente' || job.status === 'rodando'
}

// ----------------------------------------------------------------------------- transporte

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function detalhe(corpo: unknown, fallback: string): string {
  if (corpo && typeof corpo === 'object' && 'detail' in corpo) {
    const d = (corpo as { detail: unknown }).detail
    if (typeof d === 'string') return d
    // erro de validação do FastAPI: lista de {loc, msg}
    if (Array.isArray(d)) {
      return d
        .map((e) => {
          const item = e as { loc?: unknown[]; msg?: string }
          const campo = Array.isArray(item.loc) ? item.loc.filter((x) => x !== 'body').join('.') : ''
          return campo ? `${campo}: ${item.msg}` : String(item.msg ?? JSON.stringify(e))
        })
        .join('; ')
    }
    return JSON.stringify(d)
  }
  return fallback
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, cache: 'no-store' }
  if (body instanceof FormData) {
    init.body = body
  } else if (body !== undefined) {
    init.body = JSON.stringify(body)
    init.headers = { 'Content-Type': 'application/json' }
  }
  let resp: Response
  try {
    resp = await fetch(BASE + path, init)
  } catch {
    throw new ApiError(0, 'sem conexão com a API (ela está rodando na porta 8000?)')
  }
  if (resp.status === 204) return undefined as T
  const texto = await resp.text()
  let corpo: unknown = undefined
  try {
    corpo = texto ? JSON.parse(texto) : undefined
  } catch {
    corpo = texto
  }
  if (!resp.ok) {
    const fallback = typeof corpo === 'string' && corpo ? corpo : `${resp.status} ${resp.statusText}`
    throw new ApiError(resp.status, detalhe(corpo, fallback))
  }
  return corpo as T
}

const enc = encodeURIComponent

// ----------------------------------------------------------------------------- endpoints

export const api = {
  health: () => request<{ status: string }>('GET', '/health'),
  config: () => request<ConfigOut>('GET', '/config'),
  doctor: () => request<CheckOut[]>('GET', '/doctor'),

  listProjects: () => request<ProjectSummary[]>('GET', '/projects'),
  createProject: (nome: string) => request<ProjectOut>('POST', '/projects', { nome }),
  getProject: (id: string) => request<ProjectOut>('GET', `/projects/${enc(id)}`),
  renameProject: (id: string, nome: string) =>
    request<ProjectOut>('PATCH', `/projects/${enc(id)}`, { nome }),
  deleteProject: (id: string) => request<void>('DELETE', `/projects/${enc(id)}`),

  uploadClips: (id: string, files: File[]) => {
    const form = new FormData()
    for (const f of files) form.append('files', f, f.name)
    return request<ProjectOut>('POST', `/projects/${enc(id)}/clips`, form)
  },
  importFolder: (id: string, pasta: string) =>
    request<ProjectOut>('POST', `/projects/${enc(id)}/clips/import`, { pasta }),
  reorderClips: (id: string, ordem: number[]) =>
    request<ProjectOut>('PUT', `/projects/${enc(id)}/clips/order`, { ordem }),
  removeClip: (id: string, indice: number) =>
    request<ProjectOut>('DELETE', `/projects/${enc(id)}/clips/${indice}`),

  transcricao: (id: string, indice: number) =>
    request<TranscricaoOut>('GET', `/projects/${enc(id)}/clips/${indice}/transcricao`),
  rosto: (id: string, indice: number) =>
    request<RostoOut>('GET', `/projects/${enc(id)}/clips/${indice}/rosto`),
  imagens: (id: string) => request<PlanoOut>('GET', `/projects/${enc(id)}/imagens`),
  broll: (id: string) => request<BrollPreviewOut>('GET', `/projects/${enc(id)}/broll`),
  editBroll: (id: string, itemId: number, mudanca: BrollEdit) =>
    request<BrollPreviewOut>('PATCH', `/projects/${enc(id)}/broll/${itemId}`, mudanca),
  editImagem: (id: string, itemId: number, mudanca: ImagemEdit) =>
    request<PlanoOut>('PATCH', `/projects/${enc(id)}/imagens/${itemId}`, mudanca),
  setZoomActive: (id: string, zoomId: number, ativo: boolean) =>
    request<PlanoOut>('PATCH', `/projects/${enc(id)}/imagens/zooms/${zoomId}`, { ativo }),
  fileUrl: (id: string, nome: string) => `${BASE}/projects/${enc(id)}/files/${enc(nome)}`,

  createJob: (id: string, tipo: JobTipo, opcoes?: PipelineOptions) =>
    request<Job>('POST', `/projects/${enc(id)}/jobs`, opcoes ? { tipo, opcoes } : { tipo }),
  listJobs: (projetoId?: string) =>
    request<Job[]>('GET', projetoId ? `/jobs?projeto_id=${enc(projetoId)}` : '/jobs'),
  getJob: (jid: string) => request<Job>('GET', `/jobs/${enc(jid)}`),
  cancelJob: (jid: string) => request<Job>('POST', `/jobs/${enc(jid)}/cancel`),
}

/** As URLs de clipe usam o índice, que muda ao reordenar: acrescenta o arquivo para o
 * navegador não reaproveitar a miniatura/vídeo de outro clipe do cache. */
export function urlDoClipe(url: string, clip: { arquivo: string }): string {
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}f=${encodeURIComponent(clip.arquivo)}`
}

export function mensagemErro(e: unknown): string {
  if (e instanceof Error) return e.message
  return String(e)
}

// ----------------------------------------------------------------------------- formatação

/** Número no formato do Brasil: vírgula decimal (13,2). */
export function fmtNum(v: number | null | undefined, casas = 1): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toLocaleString('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas })
}

export function fmtSeg(s: number | null | undefined): string {
  if (s == null) return '—'
  const m = Math.floor(s / 60)
  const r = s - m * 60
  return m > 0 ? `${m}:${fmtNum(r).padStart(4, '0')}` : `${fmtNum(r)} s`
}

export function fmtData(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR')
}

/** Relógio mm:ss (tempo decorrido de job e tempos do player). */
export function fmtRelogio(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s) || s < 0) return '00:00'
  const total = Math.floor(s)
  const m = Math.floor(total / 60)
  return `${String(m).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

/** Lê um número do `resultado` do job (que a API devolve sem tipo fixo). */
export function numeroDoResultado(
  resultado: Record<string, unknown> | null,
  campo: string,
): number | null {
  const v = resultado?.[campo]
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/** `resultado.avisos`: frases em português sobre os clipes (não são erros). */
export function avisosDoResultado(resultado: Record<string, unknown> | null): string[] {
  const v = resultado?.avisos
  if (!Array.isArray(v)) return []
  return v.filter((a): a is string => typeof a === 'string' && a.length > 0)
}

/** `resultado.llm`: uso do LLM daquela execução (null quando não houve chamada). */
export function usoLLMDoResultado(resultado: Record<string, unknown> | null): UsoLLM | null {
  const v = resultado?.llm
  if (!v || typeof v !== 'object') return null
  const u = v as Partial<UsoLLM>
  if (typeof u.chamadas !== 'number') return null
  return {
    chamadas: u.chamadas,
    tokens_entrada: typeof u.tokens_entrada === 'number' ? u.tokens_entrada : 0,
    tokens_saida: typeof u.tokens_saida === 'number' ? u.tokens_saida : 0,
    custo_usd: typeof u.custo_usd === 'number' ? u.custo_usd : null,
    modelos: Array.isArray(u.modelos) ? u.modelos.filter((m): m is string => typeof m === 'string') : [],
  }
}

/** Linha curta do uso do LLM: `2 chamadas · 5,4 mil tokens · ≈US$ 0,0041`. */
export function fmtUsoLLM(uso: UsoLLM): string {
  const tokens = uso.tokens_entrada + uso.tokens_saida
  let texto: string
  if (tokens >= 1e6) {
    texto = `${(tokens / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} mi de tokens`
  } else if (tokens >= 1000) {
    texto = `${(tokens / 1000).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} mil tokens`
  } else {
    texto = `${tokens.toLocaleString('pt-BR')} ${tokens === 1 ? 'token' : 'tokens'}`
  }
  const partes = [
    `${uso.chamadas.toLocaleString('pt-BR')} ${uso.chamadas === 1 ? 'chamada' : 'chamadas'}`,
    texto,
  ]
  if (uso.custo_usd !== null) {
    const casas = uso.custo_usd >= 1 ? 2 : 4
    partes.push(
      `≈US$ ${uso.custo_usd.toLocaleString('pt-BR', {
        minimumFractionDigits: casas,
        maximumFractionDigits: casas,
      })}`,
    )
  }
  return partes.join(' · ')
}

/** Separa o "(detalhe: …)" que `src/errors.py` acrescenta no fim da mensagem do job. */
export function partesDoErro(mensagem: string): { texto: string; detalhe: string | null } {
  const marca = mensagem.lastIndexOf('(detalhe: ')
  if (marca < 0 || !mensagem.trimEnd().endsWith(')')) return { texto: mensagem, detalhe: null }
  const texto = mensagem.slice(0, marca).trim()
  const detalhe = mensagem.trimEnd().slice(marca + '(detalhe: '.length, -1).trim()
  if (!texto || !detalhe) return { texto: mensagem, detalhe: null }
  return { texto, detalhe }
}
