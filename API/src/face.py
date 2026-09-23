"""Rastreio de rosto por clipe: MediaPipe Face Detector + suavização (EMA e zona morta).

Uso de debug: `python -m src.face clipe.mp4 -o debug.mp4` desenha a detecção bruta
(vermelho), a caixa real do rosto (verde) e o centro da câmera (cruz azul-clara).

Coordenadas são normalizadas (0..1) em relação ao quadro de exibição do clipe. O
rastreio é isolado por clipe (a suavização reinicia em cada um) e fica em cache por
hash do arquivo + parâmetros.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import cv2
import numpy as np
import requests
from pydantic import BaseModel, Field

from src.cache import file_hash, read_json_cache, write_json_cache
from src.config import Settings, get_settings

log = logging.getLogger(__name__)

# Progresso de uma etapa longa: recebe a fração (0..1) e pode lançar para cancelar.
Progress = Callable[[float], None]

FACE_VERSION = 3
MODEL_NAME = "blaze_face_short_range.tflite"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"
)
DETECT_SIZE = 640  # lado máximo de cada recorte quadrado enviado ao detector
CENTRO = (0.5, 0.5)


class FaceParams(BaseModel):
    passo: int = Field(2, ge=1, description="detecta a cada N frames e interpola o resto")
    confianca: float = Field(0.5, gt=0, lt=1)
    alpha: float = Field(0.12, gt=0, le=1, description="EMA por frame (a 30 fps)")
    alpha_tamanho: float = Field(0.05, gt=0, le=1, description="EMA do tamanho da caixa")
    alpha_caixa: float = Field(
        0.4, gt=0, le=1, description="EMA leve da caixa real do rosto (só tira o ruído)"
    )
    zona_morta: float = Field(
        0.02, ge=0, description="movimento ignorado, em fração da largura/altura do quadro"
    )


class FaceTrack(BaseModel):
    """Rosto por frame do clipe (quadro de exibição).

    Duas leituras, para usos diferentes:
    - `cx, cy, w, h`: caminho **da câmera** — muito suavizado, contínuo, definido em todo
      frame (centro do quadro antes do 1º rosto, última posição quando o rosto some).
      A suavização é bidirecional, então a câmera começa a se mover um pouco antes do
      rosto; é o que se quer para o reenquadramento (Etapas 6 e 9).
    - `box_at(t)`: **caixa real do rosto** — só com detecção, suavização leve e sem
      antecipação; None quando não há rosto. Usada para não cobrir/cortar o rosto
      (Etapas 8 e 9).
    """

    fps: float
    largura: int
    altura: int
    cx: list[float]
    cy: list[float]
    w: list[float]
    h: list[float]
    detectado: list[bool]  # houve detecção real (ou interpolação entre detecções)
    caixa: list[list[float] | None] = Field(
        default_factory=list, description="caixa real do rosto por frame (None sem rosto)"
    )
    bruto: list[list[float] | None] = Field(
        default_factory=list, description="detecção bruta nos frames amostrados (debug)"
    )

    @property
    def n_frames(self) -> int:
        return len(self.cx)

    @property
    def cobertura(self) -> float:
        """Fração dos frames com rosto."""
        return sum(self.detectado) / self.n_frames if self.n_frames else 0.0

    def at(self, t_src: float) -> tuple[float, float, float, float]:
        """(cx, cy, w, h) normalizados no instante `t_src` (interpolado entre frames)."""
        if not self.n_frames:
            return (*CENTRO, 0.0, 0.0)
        f = min(max(t_src * self.fps, 0.0), self.n_frames - 1)
        i = int(math.floor(f))
        j = min(i + 1, self.n_frames - 1)
        k = f - i
        return tuple(  # type: ignore[return-value]
            (1 - k) * arr[i] + k * arr[j] for arr in (self.cx, self.cy, self.w, self.h)
        )

    def box_at(self, t_src: float) -> tuple[float, float, float, float] | None:
        """Caixa real do rosto (cx, cy, w, h) no frame de `t_src`; None se não há rosto."""
        if not self.caixa:
            return None
        i = min(max(int(round(t_src * self.fps)), 0), len(self.caixa) - 1)
        b = self.caixa[i]
        return tuple(b) if b is not None else None  # type: ignore[return-value]


# --------------------------------------------------------------------------- modelo


def ensure_model(settings: Settings | None = None) -> Path:
    """Caminho do modelo BlazeFace; baixa uma vez para `CACHE_DIR/models/`."""
    settings = settings or get_settings()
    path = settings.cache_dir / "models" / MODEL_NAME
    if path.exists() and path.stat().st_size > 100_000:
        return path
    log.info("Baixando o modelo de rosto (%s)...", MODEL_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(MODEL_URL, timeout=60)
    resp.raise_for_status()
    tmp = path.with_suffix(".part")
    tmp.write_bytes(resp.content)
    tmp.replace(path)
    return path


# --------------------------------------------------------------------------- detecção


def iter_frames(path: Path) -> Iterator[np.ndarray]:
    """Frames BGR do vídeo, já na orientação de exibição (OpenCV aplica a rotação)."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV não abriu {path.name}")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            yield frame
    finally:
        cap.release()


def video_fps(path: Path) -> float:
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
    finally:
        cap.release()
    return float(fps) if fps and fps > 0 else 30.0


def pick_face(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    """Entre vários rostos (cx, cy, w, h normalizados), o maior e mais central."""
    if not boxes:
        return None

    def nota(b: tuple[float, float, float, float]) -> float:
        cx, cy, w, h = b
        dist = math.hypot(cx - CENTRO[0], cy - CENTRO[1]) / math.hypot(*CENTRO)
        return w * h * (1.0 - 0.5 * dist)

    return max(boxes, key=nota)


def square_crops(largura: int, altura: int) -> list[tuple[int, int, int]]:
    """Recortes quadrados (x0, y0, lado) sobrepostos que cobrem o quadro.

    O BlazeFace de curto alcance reduz a imagem inteira para 128x128: num quadro 16:9
    um rosto de ~25% da altura vira ~10 px e não é detectado. Em recortes quadrados do
    tamanho do lado menor o rosto fica proporcionalmente maior; a sobreposição garante
    que cada rosto apareça inteiro em pelo menos um recorte.
    """
    lado = min(largura, altura)
    longo = max(largura, altura)
    n = 1 if longo == lado else math.ceil(longo / lado) + 1
    offsets = np.linspace(0, longo - lado, n).round().astype(int) if n > 1 else [0]
    if largura >= altura:
        return [(int(o), 0, lado) for o in offsets]
    return [(0, int(o), lado) for o in offsets]


def detect_raw(
    path: Path,
    params: FaceParams,
    settings: Settings,
    on_progress: Progress | None = None,
) -> tuple[float, int, int, int, list[tuple[float, float, float, float] | None]]:
    """Detecta nos frames amostrados. Devolve fps, largura, altura, nº de frames e as
    detecções (uma por frame amostrado, None quando não há rosto)."""
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    from src.clips import probe_clip

    fps = video_fps(path)
    options = vision.FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(ensure_model(settings))),
        running_mode=vision.RunningMode.VIDEO,
        min_detection_confidence=params.confianca,
    )
    amostras: list[tuple[float, float, float, float] | None] = []
    largura = altura = n = 0
    crops: list[tuple[int, int, int]] = []
    # Um detector por clipe: o modo VIDEO exige timestamps crescentes.
    # só precisa do total (e do ffprobe) para reportar progresso
    total = max(1.0, probe_clip(path).duracao * fps) if on_progress is not None else 1.0
    with vision.FaceDetector.create_from_options(options) as detector:
        for n, frame in enumerate(iter_frames(path)):
            if on_progress is not None and n % params.passo == 0:
                on_progress(min(n / total, 1.0))
            if n == 0:
                altura, largura = frame.shape[:2]
                crops = square_crops(largura, altura)
            if n % params.passo:
                continue
            base_ms = int(round(n * 1000 / fps)) * len(crops)
            boxes = []
            for k, (x0, y0, lado) in enumerate(crops):
                recorte = frame[y0 : y0 + lado, x0 : x0 + lado]
                escala = min(1.0, DETECT_SIZE / lado)
                if escala < 1:
                    recorte = cv2.resize(recorte, None, fx=escala, fy=escala)
                imagem = mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=np.ascontiguousarray(cv2.cvtColor(recorte, cv2.COLOR_BGR2RGB)),
                )
                # timestamps estritamente crescentes, um por recorte
                resultado = detector.detect_for_video(imagem, base_ms + k)
                for det in resultado.detections:
                    b = det.bounding_box
                    s = lado / recorte.shape[0]  # volta para pixels do quadro
                    boxes.append(
                        (
                            (x0 + (b.origin_x + b.width / 2) * s) / largura,
                            (y0 + (b.origin_y + b.height / 2) * s) / altura,
                            b.width * s / largura,
                            b.height * s / altura,
                        )
                    )
            amostras.append(pick_face(boxes))
        n_frames = n + 1 if largura else 0
    return fps, largura, altura, n_frames, amostras


# --------------------------------------------------------------------------- suavização


def fill_and_interpolate(
    amostras: list[tuple[float, float, float, float] | None], passo: int, n_frames: int
) -> tuple[np.ndarray, np.ndarray]:
    """Série por frame (n, 4) a partir das amostras.

    Entre duas detecções: interpolação linear. Sem detecção: mantém a última posição
    conhecida; antes da primeira detecção do clipe: centro do quadro, já com o tamanho
    da primeira detecção (tamanho zero contaminaria a suavização do tamanho).
    """
    valores = np.zeros((n_frames, 4))
    detectado = np.zeros(n_frames, dtype=bool)
    idx = [k * passo for k, a in enumerate(amostras) if a is not None and k * passo < n_frames]
    pts = [amostras[i // passo] for i in idx]
    if not idx:
        valores[:, 0:2] = CENTRO
        return valores, detectado

    frames = np.arange(n_frames)
    for col in range(4):
        valores[:, col] = np.interp(frames, idx, [p[col] for p in pts])  # type: ignore[index]
    # antes da 1ª detecção: centro; depois da última: segura (np.interp já segura)
    valores[: idx[0], 0:2] = CENTRO
    # buracos longos entre detecções: segura a última posição em vez de interpolar
    for a, b in zip(idx, idx[1:], strict=False):
        if b - a > 2 * passo:
            valores[a:b] = valores[a]
        else:
            detectado[a : b + 1] = True
    detectado[idx] = True
    # frames depois da última amostra, ainda dentro do passo dela
    detectado[idx[-1] : min(idx[-1] + passo, n_frames)] = True
    return valores, detectado


def face_boxes(valores: np.ndarray, detectado: np.ndarray, alpha: float) -> list:
    """Caixa real por frame: EMA leve bidirecional só dentro de cada trecho com rosto."""
    caixas: list = [None] * len(detectado)
    i = 0
    while i < len(detectado):
        if not detectado[i]:
            i += 1
            continue
        j = i
        while j < len(detectado) and detectado[j]:
            j += 1
        trecho = np.column_stack([ema_bidirecional(valores[i:j, col], alpha) for col in range(4)])
        for k, linha in enumerate(np.clip(trecho, 0.0, 1.0)):
            caixas[i + k] = [round(float(v), 5) for v in linha]
        i = j
    return caixas


def ema_bidirecional(x: np.ndarray, alpha: float) -> np.ndarray:
    """EMA para frente e depois para trás: suaviza sem atraso (processamento offline)."""
    if len(x) == 0:
        return x.copy()

    def passada(v: np.ndarray) -> np.ndarray:
        out = np.empty_like(v)
        acc = v[0]
        for i, val in enumerate(v):
            acc = acc + alpha * (val - acc)
            out[i] = acc
        return out

    return passada(passada(x)[::-1])[::-1]


def zona_morta(x: np.ndarray, zona: float) -> np.ndarray:
    """Só se move quando o alvo sai da zona; o movimento é contínuo (sem saltos)."""
    if len(x) == 0 or zona <= 0:
        return x.copy()
    out = np.empty_like(x)
    atual = x[0]
    for i, alvo in enumerate(x):
        diff = alvo - atual
        if abs(diff) > zona:
            atual += diff - math.copysign(zona, diff)
        out[i] = atual
    return out


def alpha_por_fps(alpha_30: float, fps: float) -> float:
    """Converte um alpha pensado para 30 fps para o fps real do clipe."""
    return 1 - (1 - alpha_30) ** (30.0 / fps) if fps > 0 else alpha_30


def smooth_track(valores: np.ndarray, params: FaceParams, fps: float) -> np.ndarray:
    """EMA bidirecional + zona morta no centro; EMA mais forte no tamanho."""
    out = valores.copy()
    if not len(out):
        return out
    a_pos = alpha_por_fps(params.alpha, fps)
    a_tam = alpha_por_fps(params.alpha_tamanho, fps)
    for col in (0, 1):
        out[:, col] = zona_morta(ema_bidirecional(out[:, col], a_pos), params.zona_morta)
    for col in (2, 3):
        out[:, col] = ema_bidirecional(out[:, col], a_tam)
    return np.clip(out, 0.0, 1.0)


# --------------------------------------------------------------------------- API


def _cache_key(path: Path, params: FaceParams) -> str:
    digest = hashlib.sha256(
        json.dumps([FACE_VERSION, params.model_dump()], sort_keys=True).encode()
    ).hexdigest()[:8]
    return f"{file_hash(path)[:32]}-{digest}"


def cached_track(
    path: str | Path, params: FaceParams | None = None, settings: Settings | None = None
) -> FaceTrack | None:
    """Rastreio do cache, sem processar; None se o clipe ainda não foi rastreado."""
    settings = settings or get_settings()
    cached = read_json_cache(
        "face", _cache_key(Path(path), params or FaceParams()), cache_dir=settings.cache_dir
    )
    return FaceTrack.model_validate(cached) if cached is not None else None


def track_faces(
    path: str | Path,
    params: FaceParams | None = None,
    *,
    settings: Settings | None = None,
    use_cache: bool = True,
    on_progress: Progress | None = None,
) -> FaceTrack:
    """Rastreio suavizado do rosto em todos os frames do clipe (em cache).

    `on_progress(fracao)` é chamado enquanto detecta (nunca com cache) e pode lançar
    exceção para cancelar.
    """
    params = params or FaceParams()
    settings = settings or get_settings()
    path = Path(path)
    key = _cache_key(path, params)

    if use_cache:
        cached = read_json_cache("face", key, cache_dir=settings.cache_dir)
        if cached is not None:
            return FaceTrack.model_validate(cached)

    started = time.perf_counter()
    fps, largura, altura, n_frames, amostras = detect_raw(path, params, settings, on_progress)
    valores, detectado = fill_and_interpolate(amostras, params.passo, n_frames)
    suave = smooth_track(valores, params, fps)
    track = FaceTrack(
        fps=fps,
        largura=largura,
        altura=altura,
        cx=np.round(suave[:, 0], 5).tolist(),
        cy=np.round(suave[:, 1], 5).tolist(),
        w=np.round(suave[:, 2], 5).tolist(),
        h=np.round(suave[:, 3], 5).tolist(),
        detectado=detectado.tolist(),
        caixa=face_boxes(valores, detectado, alpha_por_fps(params.alpha_caixa, fps)),
        bruto=[None if a is None else [round(v, 5) for v in a] for a in amostras],
    )
    log.info(
        "Rosto em %s: %d frames, %.0f%% com rosto, %.1f s",
        path.name,
        n_frames,
        100 * track.cobertura,
        time.perf_counter() - started,
    )
    if track.cobertura == 0 and n_frames:
        log.warning("%s: nenhum rosto detectado; o enquadramento fica centralizado.", path.name)
    write_json_cache("face", key, track.model_dump(), cache_dir=settings.cache_dir)
    return track


# --------------------------------------------------------------------------- debug


def render_debug(
    path: Path, output: Path, track: FaceTrack, passo: int, on_progress: Progress | None = None
) -> Path:
    """Debug: detecção bruta (vermelho), caixa real suavizada (verde) e centro da câmera
    (cruz azul-clara)."""
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{track.largura}x{track.altura}"]
    cmd += ["-r", f"{track.fps}", "-i", "-", "-i", str(path), "-map", "0:v", "-map", "1:a?"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-shortest", str(output)]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg não encontrado no PATH (rode `python -m src.doctor`)") from exc
    assert proc.stdin is not None
    W, H = track.largura, track.altura

    def caixa(img, cx, cy, w, h, cor, esp):
        p1 = (int((cx - w / 2) * W), int((cy - h / 2) * H))
        p2 = (int((cx + w / 2) * W), int((cy + h / 2) * H))
        cv2.rectangle(img, p1, p2, cor, esp)

    try:
        for i, frame in enumerate(iter_frames(path)):
            if on_progress is not None and i % 10 == 0:
                on_progress(min(i / max(track.n_frames, 1), 1.0))
            if i >= track.n_frames:
                break
            k = i // passo
            if i % passo == 0 and k < len(track.bruto) and track.bruto[k]:
                caixa(frame, *track.bruto[k], (0, 0, 255), 1)
            real = track.caixa[i] if i < len(track.caixa) else None
            if real is not None:
                caixa(frame, *real, (0, 255, 0), 3)
            c = (int(track.cx[i] * W), int(track.cy[i] * H))  # centro da câmera
            cv2.drawMarker(frame, c, (255, 200, 0), cv2.MARKER_CROSS, 28, 2)
            rotulo = "rosto" if real is not None else "sem rosto (camera mantida)"
            cv2.putText(frame, rotulo, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
    except BaseException:
        # cancelamento ou erro no meio do loop: sem matar o ffmpeg, ele ficaria
        # esperando frames e o `stderr.read()` abaixo travaria para sempre
        proc.kill()
        proc.wait()
        raise
    finally:
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg falhou no vídeo de debug: {err[-1500:]}")
    return output


def main(argv: list[str] | None = None) -> int:
    from src.logging_setup import setup_logging

    parser = argparse.ArgumentParser(prog="python -m src.face", description=__doc__)
    parser.add_argument("arquivo", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="vídeo de debug com as caixas")
    parser.add_argument("--sem-cache", action="store_true")
    parser.add_argument("--passo", type=int, default=FaceParams().passo)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    setup_logging()
    params = FaceParams(passo=args.passo)
    started = time.perf_counter()
    track = track_faces(args.arquivo, params, use_cache=not args.sem_cache)
    print(
        f"{track.n_frames} frames {track.largura}x{track.altura} @ {track.fps:.2f} fps, "
        f"rosto em {100 * track.cobertura:.0f}% ({time.perf_counter() - started:.2f} s)"
    )
    if args.output:
        render_debug(args.arquivo, args.output, track, params.passo)
        print(f"debug: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
