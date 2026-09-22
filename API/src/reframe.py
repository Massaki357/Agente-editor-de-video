"""Reenquadramento 9:16: janela de recorte que segue o rosto, com câmera suave.

A entrada é o caminho da câmera da Etapa 5 (`FaceTrack.cx/cy`, já suavizado e sem
atraso). Aqui ele vira uma janela de recorte em pixels do quadro de origem:

- quadro mais largo que 9:16 (ex.: 16:9): janela com a altura inteira do quadro,
  largura `altura * 9/16`, deslizando na horizontal;
- quadro mais alto que 9:16: janela com a largura inteira, deslizando na vertical,
  com o rosto a ~40% da altura da janela (espaço acima da cabeça);
- a janela nunca sai do quadro, e a câmera tem velocidade máxima (px/s), aplicada
  para frente e para trás e depois feita a média (limita sem atrasar).

Etapa 9 (zoom): a mesma janela com escala 1,0 → 1,15 (`window_f(t, escala)`), com
rampas suaves (`zoom_curve`). O pico de cada zoom é fixo e limitado para a caixa
real do rosto caber com folga (`plan_zooms`), então a escala nunca treme.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from pydantic import BaseModel, Field

from src.face import FaceTrack

ASPECTO = 9 / 16  # largura / altura da saída


class ReframeParams(BaseModel):
    vel_max: float = Field(
        0.5, gt=0, description="velocidade máxima da câmera, em larguras de quadro por segundo"
    )
    altura_rosto: float = Field(
        0.4, gt=0, lt=1, description="posição vertical do rosto na janela (quadro vertical)"
    )


def crop_size(largura: int, altura: int) -> tuple[int, int]:
    """Maior janela 9:16 (dimensões pares) que cabe no quadro."""
    if largura / altura > ASPECTO:
        ch = altura - altura % 2
        cw = int(round(ch * ASPECTO))
    else:
        cw = largura - largura % 2
        ch = int(round(cw / ASPECTO))
    return cw - cw % 2, min(ch - ch % 2, altura - altura % 2)


def limit_speed(x: np.ndarray, max_step: float) -> np.ndarray:
    """Limita |x[i+1] - x[i]| a `max_step`, sem atraso (média ida/volta).

    Efeito colateral: num salto isolado, a câmera anda a ~metade da velocidade
    máxima pelo dobro do tempo (a transição fica centrada no salto).
    """
    if len(x) < 2:
        return x.astype(float)

    def passada(v: np.ndarray) -> np.ndarray:
        out = np.empty(len(v))
        out[0] = v[0]
        for i in range(1, len(v)):
            out[i] = out[i - 1] + np.clip(v[i] - out[i - 1], -max_step, max_step)
        return out

    return (passada(x) + passada(x[::-1])[::-1]) / 2


class ZoomParams(BaseModel):
    escala: float = Field(1.15, ge=1.0, le=1.6, description="zoom máximo (1,15 = 15% mais perto)")
    margem_rosto: float = Field(
        0.35, ge=0, description="folga ao redor do rosto: o zoom nunca o corta"
    )
    altura_rosto: float = Field(0.4, gt=0, lt=1, description="rosto a 40% da altura da janela")


def smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


Zoom = tuple[float, float, float]  # (início, fim) em t_out e escala de pico


def zoom_curve(zooms: Sequence[Zoom], rampa: float = 0.45) -> Callable[[float], float]:
    """Escala do zoom em cada instante do vídeo final (1,0 fora dos intervalos).

    Cada zoom sobe de 1,0 até o seu pico e volta, com smoothstep (derivada zero nas
    pontas: sem tranco); o pico é constante no intervalo, então a escala nunca
    oscila. Intervalos curtos têm as rampas encurtadas para caberem.
    """
    ordenados = sorted(zooms)

    def escala(t_out: float) -> float:
        for a, b, pico in ordenados:
            if a <= t_out <= b:
                r = min(rampa, (b - a) / 2)
                k = min(smoothstep((t_out - a) / r), smoothstep((b - t_out) / r))
                return 1.0 + (pico - 1.0) * k
        return 1.0

    return escala


def plan_zooms(
    intervalos: Sequence[tuple[float, float]],
    to_src: Callable[[float], tuple[int, float]],
    cameras: Mapping[int, CameraPath],
    params: ZoomParams | None = None,
    fps: float = 30.0,
) -> list[Zoom]:
    """Pico de cada zoom (t_out): o maior que mantém o rosto real inteiro, com folga.

    Percorre o intervalo frame a frame (`to_src`: t_out → clipe, t_src) e usa o menor
    limite (`CameraPath.face_zoom_limit`) dos frames com rosto detectado. Frames sem
    detecção não limitam: o zoom segue o caminho da câmera, que guarda a última
    posição do rosto (o detector perde rostos pequenos ou de perfil). Clipe sem
    reenquadramento não tem zoom.
    """
    params = params or ZoomParams()
    zooms: list[Zoom] = []
    for a, b in intervalos:
        n = max(1, int(round((b - a) * fps)))
        limites: list[float] = [params.escala]
        for k in range(n + 1):
            clip, t_src = to_src(min(a + k / fps, b))
            camera = cameras.get(clip)
            if camera is None:
                limites = [1.0]
                break
            limite = camera.face_zoom_limit(t_src)
            if limite is not None:
                limites.append(limite)
        pico = min(limites)
        if pico >= 1.02:  # zoom imperceptível não vale a pena
            zooms.append((a, b, round(pico, 4)))
    return zooms


@dataclass(frozen=True)
class CameraPath:
    """Janela de recorte por frame do clipe de origem (pixels do quadro de exibição)."""

    largura: int  # quadro de origem
    altura: int
    fps: float
    cw: int  # tamanho da janela sem zoom
    ch: int
    x: np.ndarray  # canto superior esquerdo, por frame
    y: np.ndarray
    fy: np.ndarray | None = None  # centro vertical do rosto (caminho suave), por frame
    rosto: FaceTrack | None = None
    zoom: ZoomParams = field(default_factory=ZoomParams)

    def _interp(self, arr: np.ndarray, t_src: float) -> float:
        n = len(arr)
        f = min(max(t_src * self.fps, 0.0), n - 1)
        i = int(math.floor(f))
        j = min(i + 1, n - 1)
        k = f - i
        return float((1 - k) * arr[i] + k * arr[j])

    def window(self, t_src: float) -> tuple[int, int, int, int]:
        """(x, y, w, h) inteiros da janela sem zoom no instante `t_src`."""
        x, y, cw, ch = self.window_f(t_src)
        return int(round(x)), int(round(y)), int(cw), int(ch)

    def window_f(self, t_src: float, escala: float = 1.0) -> tuple[float, float, float, float]:
        """Janela (x, y, w, h) em pixels fracionários; `escala` > 1 aproxima (zoom).

        O zoom encolhe a janela para cw/escala x ch/escala: centro horizontal da
        câmera, rosto (caminho suave) a ~40% da altura, deslocada o mínimo para o rosto
        suave caber inteiro e nunca saindo do quadro. Frações evitam o tremor de 1 px
        que o arredondamento causaria enquanto a escala muda.
        """
        x = min(max(self._interp(self.x, t_src), 0.0), self.largura - self.cw)
        y = min(max(self._interp(self.y, t_src), 0.0), self.altura - self.ch)
        if escala <= 1.0 + 1e-9:
            return x, y, float(self.cw), float(self.ch)
        zw, zh = self.cw / escala, self.ch / escala
        zx = x + self.cw / 2 - zw / 2
        fy = self._interp(self.fy, t_src) if self.fy is not None else y + self.ch / 2
        zy = fy - self.zoom.altura_rosto * zh
        if self.rosto is not None:  # rosto (suave) inteiro dentro da janela
            fcx, fcy, fw, fh = self.rosto.at(t_src)
            fx0, fx1 = (fcx - fw / 2) * self.largura, (fcx + fw / 2) * self.largura
            fy0, fy1 = (fcy - fh / 2) * self.altura, (fcy + fh / 2) * self.altura
            zx = min(max(zx, fx1 - zw), fx0)
            zy = min(max(zy, fy1 - zh), fy0)
        zx = min(max(zx, 0.0), self.largura - zw)
        zy = min(max(zy, 0.0), self.altura - zh)
        return zx, zy, zw, zh

    def face_zoom_limit(self, t_src: float) -> float | None:
        """Maior escala em que a caixa real do rosto, com folga, cabe na janela.

        None sem rosto detectado nesse frame.
        """
        caixa = self.rosto.box_at(t_src) if self.rosto is not None else None
        if caixa is None:
            return None
        folga = 1 + self.zoom.margem_rosto
        fw = max(caixa[2] * self.largura * folga, 1.0)
        fh = max(caixa[3] * self.altura * folga, 1.0)
        return max(1.0, min(self.cw / fw, self.ch / fh))

    def to_output(
        self,
        box: tuple[float, float, float, float],
        t_src: float,
        saida: tuple[int, int],
        escala: float = 1.0,
    ) -> tuple[float, float, float, float]:
        """Caixa normalizada do quadro de origem (cx, cy, w, h) → pixels da saída.

        Usada pelas Etapas 8 e 9 para saber onde o rosto aparece no 1080x1920, com o
        zoom daquele instante, se houver.
        """
        if escala <= 1.0 + 1e-9:
            x, y, cw, ch = self.window(t_src)  # o mesmo recorte inteiro do render
        else:
            x, y, cw, ch = self.window_f(t_src, escala)
        ow, oh = saida
        sx, sy = ow / cw, oh / ch
        cx, cy, w, h = box
        return (
            (cx * self.largura - x) * sx,
            (cy * self.altura - y) * sy,
            w * self.largura * sx,
            h * self.altura * sy,
        )


def camera_path(
    track: FaceTrack | None,
    largura: int,
    altura: int,
    fps: float = 30.0,
    params: ReframeParams | None = None,
    zoom: ZoomParams | None = None,
) -> CameraPath:
    """Caminho da janela 9:16 a partir do rastreio (sem rastreio: janela central)."""
    params = params or ReframeParams()
    cw, ch = crop_size(largura, altura)
    if track is not None and track.n_frames:
        fps = track.fps
        cx = np.asarray(track.cx) * largura
        cy = np.asarray(track.cy) * altura
    else:
        cx = np.array([largura / 2])
        cy = np.array([altura / 2])

    x = np.clip(cx - cw / 2, 0, largura - cw)
    if ch < altura:  # quadro mais alto que 9:16: desliza na vertical, rosto a ~40%
        y = np.clip(cy - params.altura_rosto * ch, 0, altura - ch)
    else:
        y = np.zeros_like(x)

    passo = params.vel_max * largura / fps
    x = np.clip(limit_speed(x, passo), 0, largura - cw)
    y = np.clip(limit_speed(y, params.vel_max * altura / fps), 0, altura - ch)
    fy = limit_speed(cy, params.vel_max * altura / fps)
    rosto = track if track is not None and track.n_frames else None
    return CameraPath(largura, altura, fps, cw, ch, x, y, fy, rosto, zoom or ZoomParams())
