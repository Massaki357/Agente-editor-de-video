import { api, type ProjectOut } from '../api'

export default function Result({ projeto }: { projeto: ProjectOut }) {
  const url = projeto.video_final_url
  return (
    <section className="cartao">
      <h3>Resultado</h3>
      {url ? (
        <>
          <video key={url} src={url} controls preload="metadata" className="player final" />
          <p>
            <a href={url} download>
              baixar final.mp4
            </a>
          </p>
        </>
      ) : (
        <p className="suave">Nenhum vídeo final ainda. Use "Gerar vídeo".</p>
      )}
      {projeto.arquivos.length > 0 && (
        <>
          <h4>Arquivos gerados</h4>
          <ul className="compacta">
            {projeto.arquivos.map((a) => (
              <li key={a}>
                <a href={api.fileUrl(projeto.id, a)} target="_blank" rel="noreferrer">
                  {a}
                </a>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
