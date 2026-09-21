"""Modelo do projeto/timeline (Pydantic) e persistência em `project.json`.

Tempos de `trechos` estão no tempo **original** do clipe (t_src). `offset` é o
instante do vídeo final (t_out) em que o clipe começa: a soma das durações
mantidas dos clipes anteriores. Imagens e zooms estão em t_out.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.cache import atomic_write_text

Trecho = tuple[float, float]

PROJECT_VERSION = 1


class _Modelo(BaseModel):
    # NaN/inf passariam nas comparações dos validadores e contaminariam os offsets.
    model_config = ConfigDict(allow_inf_nan=False)


class ClipMeta(_Modelo):
    """Metadados lidos via ffprobe."""

    duracao: float
    largura: int
    altura: int
    fps: float
    tem_audio: bool
    rotacao: int = 0  # já aplicada em largura/altura (dimensões de exibição)


class Clip(_Modelo):
    arquivo: str
    trechos: list[Trecho] = Field(default_factory=list)
    offset: float = 0.0
    meta: ClipMeta | None = None

    @field_validator("trechos")
    @classmethod
    def _trechos_validos(cls, trechos: list[Trecho]) -> list[Trecho]:
        anterior_fim = float("-inf")
        for inicio, fim in trechos:
            if inicio < 0 or fim <= inicio:
                raise ValueError(f"trecho inválido: [{inicio}, {fim}]")
            if inicio < anterior_fim:
                raise ValueError("trechos devem estar em ordem e sem sobreposição")
            anterior_fim = fim
        return trechos

    @model_validator(mode="after")
    def _trechos_dentro_do_clipe(self) -> Clip:
        if self.meta and self.trechos and self.trechos[-1][1] > self.meta.duracao + 1e-3:
            raise ValueError(
                f"{self.arquivo}: trecho termina em {self.trechos[-1][1]} "
                f"mas o clipe tem {self.meta.duracao} s"
            )
        return self

    @property
    def duracao_mantida(self) -> float:
        return sum(fim - inicio for inicio, fim in self.trechos)

    @property
    def nome(self) -> str:
        return Path(self.arquivo).name


class Imagem(_Modelo):
    inicio: float
    duracao: float
    query: str


class Zoom(_Modelo):
    inicio: float
    duracao: float


class Timeline(_Modelo):
    clipes: list[Clip] = Field(default_factory=list)
    imagens: list[Imagem] = Field(default_factory=list)
    zooms: list[Zoom] = Field(default_factory=list)
    legendas: str | None = None

    @property
    def duracao_total(self) -> float:
        return sum(c.duracao_mantida for c in self.clipes)

    def recalcular_offsets(self) -> None:
        t_out = 0.0
        for clip in self.clipes:
            clip.offset = round(t_out, 6)
            t_out += clip.duracao_mantida

    def mover_clipe(self, de: int, para: int) -> None:
        """Move o clipe da posição `de` para `para` (como arrastar numa lista)."""
        n = len(self.clipes)
        if not (0 <= de < n and 0 <= para < n):
            raise IndexError(f"mover_clipe({de}, {para}) fora da lista de {n} clipes")
        clip = self.clipes.pop(de)
        self.clipes.insert(para, clip)
        self._timeline_mudou()

    def reordenar(self, nova_ordem: list[int]) -> None:
        """Reordena pelos índices atuais, ex.: `[2, 0, 1]` põe o terceiro clipe primeiro."""
        if sorted(nova_ordem) != list(range(len(self.clipes))):
            raise ValueError(f"ordem inválida: {nova_ordem}")
        self.clipes = [self.clipes[i] for i in nova_ordem]
        self._timeline_mudou()

    def remover_clipe(self, indice: int) -> Clip:
        clip = self.clipes.pop(indice)
        self._timeline_mudou()
        return clip

    def adicionar_clipe(self, clip: Clip) -> None:
        self.clipes.append(clip)
        self._timeline_mudou()

    def _timeline_mudou(self) -> None:
        # Imagens e zooms estão em t_out: mudar a ordem invalida o plano criativo.
        self.imagens.clear()
        self.zooms.clear()
        self.legendas = None
        self.recalcular_offsets()


class Project(_Modelo):
    versao: int = PROJECT_VERSION
    timeline: Timeline = Field(default_factory=Timeline)

    def salvar(self, path: str | Path) -> Path:
        """Grava o JSON; caminhos de clipes dentro da pasta do projeto viram relativos."""
        path = Path(path)
        base = path.parent.resolve()
        data = self.model_copy(deep=True)
        for clip in data.timeline.clipes:
            clip.arquivo = _relativo(clip.arquivo, base)
        atomic_write_text(path, data.model_dump_json(indent=2))
        return path

    @classmethod
    def carregar(cls, path: str | Path) -> Project:
        """Lê o JSON; caminhos relativos são resolvidos a partir da pasta do projeto."""
        path = Path(path)
        project = cls.model_validate_json(path.read_text(encoding="utf-8"))
        base = path.parent.resolve()
        for clip in project.timeline.clipes:
            if not Path(clip.arquivo).is_absolute():
                clip.arquivo = str(base / clip.arquivo)
        project.timeline.recalcular_offsets()
        return project


def _relativo(arquivo: str, base: Path) -> str:
    p = Path(arquivo)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.resolve().relative_to(base).as_posix()
    except ValueError:
        return str(p)
