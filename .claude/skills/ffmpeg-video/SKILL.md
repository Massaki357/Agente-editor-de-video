---
name: ffmpeg-video
description: Receitas de FFmpeg/ffprobe e pipe OpenCV→FFmpeg usadas no editor de vídeos (metadados, silencedetect, cortes, concat, fades, legendas .ass, overlay com enable). Use ao escrever clips.py, cuts.py, render.py, captions.py ou images.py.
---

# FFmpeg no editor de vídeos

Sempre chame FFmpeg via `subprocess.run([...], check=True, capture_output=True)` com lista de argumentos (nunca `shell=True`) e `-hide_banner -nostdin -y`. Caminhos no Windows podem ter espaços; a lista de argumentos resolve isso.

## Metadados (Etapa 1)
```
ffprobe -v error -print_format json -show_format -show_streams <arquivo>
```
Duração: `format.duration`. Vídeo: stream `codec_type == "video"` → `width`, `height`, `avg_frame_rate` ("30000/1001" → use `fractions.Fraction`). Considere `tags.rotate` ou `side_data_list[].rotation` (celular em pé). `tem_audio` = existe stream `audio`.

## Áudio para o Whisper (Etapa 2)
`ffmpeg -i in.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le out.wav`

## Silêncios (Etapa 3)
`ffmpeg -i in.mp4 -vn -af silencedetect=noise=-35dB:d=0.4 -f null -`
Faça o parse do **stderr**: `silence_start: X` / `silence_end: Y | silence_duration: Z`. Um silêncio sem `silence_end` vai até o fim do arquivo.

## Cortar e normalizar trechos
Por trecho `[a, b]` use `-ss a -to b` **depois** de `-i` (preciso ao frame) ou `trim/atrim` + `setpts=PTS-STARTPTS` / `asetpts=PTS-STARTPTS` num filter_complex. Normalize com `fps=30`, `aresample=48000` e `-ar 48000 -ac 2`.
Fade em cada emenda (20 a 30 ms): `afade=t=in:st=0:d=0.025,afade=t=out:st=<dur-0.025>:d=0.025`.
Concat de segmentos já normalizados: filtro `concat=n=N:v=1:a=1` (mais seguro que o demuxer quando os parâmetros variam).

## Passada 1: frames do OpenCV para o FFmpeg (Etapa 6)
```
ffmpeg -f rawvideo -pix_fmt bgr24 -s 1080x1920 -r 30 -i - \
       -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p video_only.mp4
```
Escreva `frame.tobytes()` em `proc.stdin`; feche o stdin e `proc.wait()`. Leia o stderr em uma thread (ou redirecione para um arquivo) para o pipe não travar.

## Passada 2: legendas e overlays (Etapas 7 e 8)
- Legendas: `ass=legendas.ass:fontsdir=fonts`. No Windows, escape o caminho no filtro: `C\:/pasta/legendas.ass` (dois-pontos com `\`, barras `/`). O mais simples é rodar com `cwd` na pasta do job e usar caminho relativo.
- Imagem: `[0:v][1:v]overlay=x=X:y=Y:enable='between(t,A,B)'`. Para o fade, use `-loop 1 -t D -i img.png` + `format=rgba,fade=t=in:st=0:d=0.2:alpha=1,fade=t=out:st=D-0.2:d=0.2:alpha=1`, deslocado com `setpts=PTS+A/TB`.
- Saída final: `-c:v libx264 -crf 18 -pix_fmt yuv420p -r 30 -c:a aac -b:a 192k -ar 48000 -movflags +faststart`.

## Vídeos sintéticos para testes
```
ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30:duration=3 \
       -f lavfi -i sine=frequency=440:sample_rate=48000:duration=3 -shortest -c:v libx264 -c:a aac out.mp4
```
Para testar o silencedetect, alterne `sine` e `anullsrc` com o filtro `concat`.
