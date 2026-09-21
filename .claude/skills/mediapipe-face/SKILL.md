---
name: mediapipe-face
description: Rastreio de rosto com MediaPipe (API Tasks) para o reenquadramento 9:16, incluindo suavização EMA, zona morta, escolha do rosto e cache. Use ao implementar src/face.py e src/reframe.py (Etapas 5, 6 e 9).
---

> **Layout:** o backend fica em `API/` (FastAPI em `API/src/api/`); caminhos `src/...` e `tests/...` neste texto são relativos a `API/`, e todo comando `uv run ...` roda dentro de `API/`. O frontend fica em `frontend/`. `samples/` e `output/` ficam na raiz do repositório.

# Rastreio de rosto (MediaPipe)

## Versão instalada: mediapipe 1.0.x
A API legada `mp.solutions.face_detection` **não existe** nessa versão (conferido: `hasattr(mp, "solutions")` é `False`). Use a **API Tasks** (confira no Context7 `/google-ai-edge/mediapipe` antes de codar):

```python
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

options = vision.FaceDetectorOptions(
    base_options=BaseOptions(model_asset_path=str(model_path)),
    running_mode=vision.RunningMode.VIDEO,
    min_detection_confidence=0.5,
)
with vision.FaceDetector.create_from_options(options) as detector:
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    result = detector.detect_for_video(image, timestamp_ms)  # timestamps estritamente crescentes
    for det in result.detections:
        box = det.bounding_box  # origin_x, origin_y, width, height em pixels
```
- Modelo: `blaze_face_short_range.tflite` (rosto a até ~2 m, o caso típico de quem fala para a câmera). Baixe uma vez para `CACHE_DIR/models/` a partir do link oficial de modelos do MediaPipe Face Detector e registre o download no doctor.
- Crie **um detector por clipe** (o modo VIDEO exige timestamps crescentes, e a suavização reinicia a cada clipe).

## Pipeline de `face.py`
1. Amostre a cada 2 a 3 frames. Converta a caixa para coordenadas normalizadas (0..1) do frame original.
2. Vários rostos: pontue por `área * (1 - distância_ao_centro)` e fique com o maior.
3. Sem rosto: repita a última posição conhecida; se o clipe começar sem rosto, use o centro do quadro (`cx=0.5, cy=0.5`), com o tamanho da 1ª detecção.
4. Interpole linearmente os frames não amostrados.
5. Suavize: zona morta (ignore deslocamento < ~2% da largura) e depois EMA (`alpha` ≈ 0,1 a 0,2 a 30 fps).
6. Cache em `CACHE_DIR/face/<hash>.json`: lista por frame `{t, cx, cy, w, h, detectado}` mais os parâmetros usados (mudar um parâmetro invalida o cache).

## Reenquadramento (reframe.py)
- Janela 9:16 com altura = altura do vídeo (horizontal); largura = `h * 9 / 16`. Centro x = `cx` do rosto, com o limite `[w/2, W - w/2]`.
- Câmera suave: limite a velocidade (px/frame) além da EMA.
- Zoom (Etapa 9): escala 1,0 → 1,15 com easing (`smoothstep`), sem cortar a caixa do rosto e sem sair do quadro.
- `FaceTrack.cx/cy/w/h` é o caminho da câmera (suave, com antecipação); `FaceTrack.box_at(t)` é a caixa real do rosto (None sem rosto). Converta a caixa real para o espaço 1080x1920 final; as Etapas 8 e 9 a usam para não cobrir nem cortar o rosto.
