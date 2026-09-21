"""Reenquadramento 9:16: janela de recorte que segue o rosto, com câmera suave.

A entrada é o caminho da câmera da Etapa 5 (`FaceTrack.cx/cy`, já suavizado e sem
atraso). Aqui ele vira uma janela de recorte em pixels do quadro de origem:

- quadro mais largo que 9:16 (ex.: 16:9): janela com a altura inteira do quadro,
  largura `altura * 9/16`, deslizando na horizontal;
- quadro mais alto que 9:16: janela com a largura inteira, deslizando na vertical,
  com o rosto a ~40% da altura da janela (espaço acima da cabeça);
- a janela nunca sai do quadro, e a câmera tem velocidade máxima (px/s), aplicada
  para frente e para trás e depois feita a média (limita sem atrasar).

A Etapa 9 (zoom) reaproveita isto com uma janela menor centrada no mesmo ponto.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

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


@dataclass(frozen=True)
class CameraPath:
    """Janela de recorte por frame do clipe de origem (pixels do quadro de exibição)."""

    largura: int  # quadro de origem
    altura: int
    fps: float
    cw: int  # tamanho da janela
    ch: int
    x: np.ndarray  # canto superior esquerdo, por frame
    y: np.ndarray

    def window(self, t_src: float) -> tuple[int, int, int, int]:
        """(x, y, w, h) inteiros no instante `t_src` (interpolado entre frames)."""
        n = len(self.x)
        f = min(max(t_src * self.fps, 0.0), n - 1)
        i = int(math.floor(f))
        j = min(i + 1, n - 1)
        k = f - i
        x = (1 - k) * self.x[i] + k * self.x[j]
        y = (1 - k) * self.y[i] + k * self.y[j]
        x = int(round(min(max(x, 0), self.largura - self.cw)))
        y = int(round(min(max(y, 0), self.altura - self.ch)))
        return x, y, self.cw, self.ch

    def to_output(
        self, box: tuple[float, float, float, float], t_src: float, saida: tuple[int, int]
    ) -> tuple[float, float, float, float]:
        """Caixa normalizada do quadro de origem (cx, cy, w, h) → pixels da saída.

        Usada pelas Etapas 8 e 9 para saber onde o rosto aparece no 1080x1920.
        """
        x, y, cw, ch = self.window(t_src)
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
    return CameraPath(largura, altura, fps, cw, ch, x, y)
