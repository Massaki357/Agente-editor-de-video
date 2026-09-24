"""Fallback de estabilização por fluxo óptico, sem depender de libvidstab.

Passada 1: pontos de Shi-Tomasi + Lucas-Kanade + afim robusta entre quadros.
Passada 2: suaviza a trajetória acumulada, compensa com warpAffine e recorta
as bordas. O FFmpeg codifica H.264 e copia o áudio original no fim.
"""

from __future__ import annotations

import logging
import math
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)
Progresso = Callable[[str, float], None]


def _cinza_reduzido(frame: np.ndarray, escala: float) -> np.ndarray:
    cinza = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if escala < 1:
        cinza = cv2.resize(cinza, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
    return cinza


def _movimento(anterior: np.ndarray, atual: np.ndarray, escala: float) -> np.ndarray:
    """Deslocamento do centro da cena (x, y, ângulo) entre dois quadros."""
    pontos = cv2.goodFeaturesToTrack(
        anterior, maxCorners=250, qualityLevel=0.01, minDistance=12, blockSize=3
    )
    if pontos is None or len(pontos) < 4:
        return np.zeros(3, dtype=np.float64)
    proximos, status, _ = cv2.calcOpticalFlowPyrLK(anterior, atual, pontos, None)
    if proximos is None or status is None:
        return np.zeros(3, dtype=np.float64)
    bons = status.reshape(-1).astype(bool)
    if np.count_nonzero(bons) < 4:
        return np.zeros(3, dtype=np.float64)
    matriz, _ = cv2.estimateAffinePartial2D(
        pontos[bons].reshape(-1, 2),
        proximos[bons].reshape(-1, 2),
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
        maxIters=200,
    )
    if matriz is None:
        return np.zeros(3, dtype=np.float64)
    centro = np.array([(anterior.shape[1] - 1) / 2, (anterior.shape[0] - 1) / 2])
    deslocamento = matriz[:, :2] @ centro + matriz[:, 2] - centro
    angulo = math.atan2(float(matriz[1, 0]), float(matriz[0, 0]))
    if not np.isfinite(deslocamento).all() or not math.isfinite(angulo):
        return np.zeros(3, dtype=np.float64)
    largura, altura = anterior.shape[1] / escala, anterior.shape[0] / escala
    if (
        abs(deslocamento[0] / escala) > largura * 0.2
        or abs(deslocamento[1] / escala) > altura * 0.2
    ):
        return np.zeros(3, dtype=np.float64)
    if abs(angulo) > math.radians(15):
        return np.zeros(3, dtype=np.float64)
    return np.array([deslocamento[0] / escala, deslocamento[1] / escala, angulo])


def _analisar(entrada: Path, on_progress: Progresso | None):
    captura = cv2.VideoCapture(str(entrada))
    try:
        ok, frame = captura.read()
        if not ok or frame is None:
            raise ValueError(f"vídeo de entrada inválido ou sem quadros: {entrada}")
        altura, largura = frame.shape[:2]
        fps = float(captura.get(cv2.CAP_PROP_FPS))
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError(f"vídeo sem FPS válido para o fallback OpenCV: {entrada}")
        total_estimado = float(captura.get(cv2.CAP_PROP_FRAME_COUNT))
        estimativa = max(1, int(total_estimado)) if math.isfinite(total_estimado) else 1
        escala = min(1.0, 640 / max(largura, altura))
        anterior = _cinza_reduzido(frame, escala)
        trajetoria = [np.zeros(3, dtype=np.float64)]
        if on_progress:
            on_progress("análise", 0.0)
        while True:
            ok, frame = captura.read()
            if not ok:
                break
            atual = _cinza_reduzido(frame, escala)
            trajetoria.append(trajetoria[-1] + _movimento(anterior, atual, escala))
            anterior = atual
            if on_progress:
                on_progress("análise", min(len(trajetoria) / estimativa, 0.99))
        if on_progress:
            on_progress("análise", 1.0)
        return np.stack(trajetoria), fps, (largura, altura)
    finally:
        captura.release()


def _correcoes(trajetoria: np.ndarray, raio: int, largura: int, altura: int) -> np.ndarray:
    """Trajetória ideal menos a real: deslocamento a aplicar em cada quadro."""
    preenchida = np.pad(trajetoria, ((raio, raio), (0, 0)), mode="edge")
    filtro = np.ones(2 * raio + 1) / (2 * raio + 1)
    suave = np.column_stack(
        [np.convolve(preenchida[:, coluna], filtro, mode="valid") for coluna in range(3)]
    )
    suave -= suave[0]  # o primeiro quadro é a referência, sem salto inicial
    correcao = suave - trajetoria
    correcao[:, 0] = np.clip(correcao[:, 0], -largura * 0.2, largura * 0.2)
    correcao[:, 1] = np.clip(correcao[:, 1], -altura * 0.2, altura * 0.2)
    correcao[:, 2] = np.clip(correcao[:, 2], -math.radians(12), math.radians(12))
    return correcao


def _margens(correcoes: np.ndarray, largura: int, altura: int, crop_percent: float | None):
    """Margens que cobrem translação e rotação de todos os quadros."""
    angulo = np.abs(correcoes[:, 2])
    seno = np.sin(angulo)
    um_menos_cosseno = 1 - np.cos(angulo)
    margem_x = np.max(np.abs(correcoes[:, 0]) + seno * altura / 2 + um_menos_cosseno * largura / 2)
    margem_y = np.max(np.abs(correcoes[:, 1]) + seno * largura / 2 + um_menos_cosseno * altura / 2)
    extra = (crop_percent or 0) / 200
    x = min(math.ceil(margem_x + 2 + extra * largura), max(0, largura // 2 - 2))
    y = min(math.ceil(margem_y + 2 + extra * altura), max(0, altura // 2 - 2))
    return x, y


def _transformar(frame: np.ndarray, correcao: np.ndarray, crop_x: int, crop_y: int) -> np.ndarray:
    altura, largura = frame.shape[:2]
    dx, dy, angulo = correcao
    c, s = math.cos(angulo), math.sin(angulo)
    centro = np.array([largura / 2, altura / 2])
    rotacao = np.array([[c, -s], [s, c]])
    trans = centro - rotacao @ centro + np.array([dx, dy])
    matriz = np.column_stack((rotacao, trans)).astype(np.float32)
    corrigido = cv2.warpAffine(
        frame,
        matriz,
        (largura, altura),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )
    recortado = corrigido[crop_y : altura - crop_y, crop_x : largura - crop_x]
    return cv2.resize(recortado, (largura, altura), interpolation=cv2.INTER_LINEAR)


def _escrever_video(
    entrada: Path,
    sem_audio: Path,
    correcoes: np.ndarray,
    fps: float,
    dimensoes: tuple[int, int],
    margens: tuple[int, int],
    on_progress: Progresso | None,
) -> None:
    largura, altura = dimensoes
    captura = cv2.VideoCapture(str(entrada))
    writer = cv2.VideoWriter(
        str(sem_audio), cv2.VideoWriter_fourcc(*"mp4v"), fps, (largura, altura)
    )
    if not writer.isOpened():
        captura.release()
        raise RuntimeError("OpenCV não conseguiu criar o vídeo temporário MP4")
    try:
        if on_progress:
            on_progress("estabilização", 0.0)
        for indice, correcao in enumerate(correcoes):
            ok, frame = captura.read()
            if not ok:
                raise RuntimeError(f"vídeo terminou antes do quadro {indice + 1}: {entrada}")
            writer.write(_transformar(frame, correcao, *margens))
            if on_progress:
                on_progress("estabilização", 0.9 * (indice + 1) / len(correcoes))
    finally:
        captura.release()
        writer.release()
    if not sem_audio.is_file() or sem_audio.stat().st_size == 0:
        raise RuntimeError("OpenCV não gerou o vídeo temporário")


def render_opencv(
    entrada: Path,
    saida: Path,
    smoothing_frames: int,
    crop_percent: float | None,
    on_progress: Progresso | None,
) -> None:
    """Estabiliza com OpenCV e muxa o áudio original com FFmpeg, atomicamente."""
    trajetoria, fps, dimensoes = _analisar(entrada, on_progress)
    correcoes = _correcoes(trajetoria, smoothing_frames, *dimensoes)
    margens = _margens(correcoes, *dimensoes, crop_percent)
    log.info(
        "Fallback OpenCV: %s quadros, %.3f fps, crop %s px × %s px",
        len(correcoes),
        fps,
        *margens,
    )
    saida.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="opencv_stab_", dir=saida.parent) as tmp:
        tmp = Path(tmp)
        sem_audio, temporario = tmp / "sem_audio.mp4", tmp / "estabilizado.mp4"
        _escrever_video(entrada, sem_audio, correcoes, fps, dimensoes, margens, on_progress)
        comando = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(sem_audio),
            "-i",
            str(entrada),
            "-map",
            "0:v:0",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            str(temporario),
        ]
        proc = subprocess.run(comando, capture_output=True, text=True)
        if proc.returncode != 0 or not temporario.is_file():
            detalhe = (proc.stderr or "sem detalhe do FFmpeg").strip().splitlines()
            raise RuntimeError(f"FFmpeg não conseguiu copiar o áudio: {' | '.join(detalhe[-3:])}")
        temporario.replace(saida)
    if on_progress:
        on_progress("estabilização", 1.0)
