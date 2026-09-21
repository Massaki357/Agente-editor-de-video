import { useCallback, useEffect, useState } from 'react'
import { api, mensagemErro, type CheckOut, type ConfigOut } from '../api'
import ErrorBox from './ErrorBox'

interface Props {
  config: ConfigOut | null
}

export default function Header({ config }: Props) {
  const [online, setOnline] = useState<boolean | null>(null)
  const [aberto, setAberto] = useState(false)
  const [checks, setChecks] = useState<CheckOut[] | null>(null)
  const [carregando, setCarregando] = useState(false)
  const [erro, setErro] = useState<string | null>(null)

  useEffect(() => {
    let vivo = true
    const checar = () =>
      api
        .health()
        .then(() => vivo && setOnline(true))
        .catch(() => vivo && setOnline(false))
    checar()
    const t = setInterval(checar, 10000)
    return () => {
      vivo = false
      clearInterval(t)
    }
  }, [])

  const rodarDoctor = useCallback(async () => {
    setCarregando(true)
    setErro(null)
    try {
      setChecks(await api.doctor())
    } catch (e) {
      setErro(mensagemErro(e))
    } finally {
      setCarregando(false)
    }
  }, [])

  const alternar = () => {
    const novo = !aberto
    setAberto(novo)
    if (novo && checks === null) void rodarDoctor()
  }

  return (
    <header className="cabecalho">
      <div className="linha">
        <h1>Editor de vídeos</h1>
        <span className={`selo ${online ? 'ok' : online === false ? 'falta' : ''}`}>
          API: {online === null ? 'verificando…' : online ? 'no ar' : 'fora do ar'}
        </span>
        <a href="/docs" target="_blank" rel="noreferrer">
          documentação da API (/docs)
        </a>
        <button onClick={alternar}>{aberto ? 'Ocultar ambiente' : 'Ambiente (doctor e config)'}</button>
      </div>
      {aberto && (
        <div className="painel">
          <h3>Configuração</h3>
          {config ? (
            <ul className="compacta">
              <li>Modelo LLM: {config.llm_model}</li>
              <li>
                Whisper: {config.whisper_model} ({config.whisper_device})
              </li>
              <li>
                Saída: {config.saida.largura}×{config.saida.altura} @ {config.saida.fps} fps
              </li>
            </ul>
          ) : (
            <p>configuração indisponível</p>
          )}
          <h3>
            Doctor{' '}
            <button onClick={rodarDoctor} disabled={carregando}>
              {carregando ? 'verificando…' : 'verificar de novo'}
            </button>
          </h3>
          <ErrorBox erro={erro} />
          {checks && (
            <table>
              <tbody>
                {checks.map((c) => (
                  <tr key={c.nome}>
                    <td>
                      <span className={`selo ${c.status.toLowerCase()}`}>{c.status}</span>
                    </td>
                    <td>{c.nome}</td>
                    <td className="suave">{c.detalhe}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </header>
  )
}
