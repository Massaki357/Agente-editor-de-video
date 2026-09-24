"""Projetos da API em disco: `DATA_DIR/projects/<id>/`.

info.json      nome e datas
project.json   o `Project` (timeline, trechos, metadados dos clipes)
clips/         vídeos enviados por upload (importados de pasta ficam no lugar)
saida/         vídeo final, vídeos de debug e miniaturas
"""

from __future__ import annotations

import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from src.cache import atomic_write_text
from src.editing.project_schema import com_plano, plano_do_documento
from src.images import PlanoImagens, save_plan
from src.project import Project


class ProjectInfo(BaseModel):
    id: str
    nome: str
    criado: datetime
    atualizado: datetime


class ProjectNotFound(KeyError):
    pass


def _agora() -> datetime:
    return datetime.now(UTC)


class ProjectStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    # ------------------------------------------------------------------ caminhos

    def dir(self, pid: str) -> Path:
        if not pid.isalnum():  # ids são hex; evita "../" no caminho
            raise ProjectNotFound(pid)
        return self.root / pid

    def clips_dir(self, pid: str) -> Path:
        return self.dir(pid) / "clips"

    def saida_dir(self, pid: str) -> Path:
        path = self.dir(pid) / "saida"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def plan_path(self, pid: str) -> Path:
        """Caminho do plano legado (mantido só para migração/compatibilidade)."""
        return self.dir(pid) / "plano_imagens.json"

    def load_plan(self, pid: str) -> PlanoImagens | None:
        """Lê exclusivamente o plano canônico do project.json."""
        with self.lock(pid):
            project = self.load(pid)
            return plano_do_documento(project.documento)

    def save_plan(self, pid: str, plano: PlanoImagens) -> None:
        """O project.json é a fonte da verdade; espelha só sidecars já existentes."""
        with self.lock(pid):
            project = self.load(pid)
            project.documento = com_plano(project.documento, plano)
            self.save(pid, project)
            if self.plan_path(pid).exists():
                save_plan(plano, self.plan_path(pid))

    def lock(self, pid: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(pid, threading.RLock())

    # ------------------------------------------------------------------ CRUD

    def create(self, nome: str) -> ProjectInfo:
        pid = uuid.uuid4().hex[:10]
        agora = _agora()
        info = ProjectInfo(id=pid, nome=nome.strip() or "Sem nome", criado=agora, atualizado=agora)
        self.clips_dir(pid).mkdir(parents=True)
        self._write_info(info)
        Project().salvar(self.dir(pid) / "project.json")
        return info

    def list(self) -> list[ProjectInfo]:
        infos = []
        for path in self.root.glob("*/info.json"):
            try:
                infos.append(ProjectInfo.model_validate_json(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return sorted(infos, key=lambda i: i.atualizado, reverse=True)

    def info(self, pid: str) -> ProjectInfo:
        path = self.dir(pid) / "info.json"
        if not path.exists():
            raise ProjectNotFound(pid)
        return ProjectInfo.model_validate_json(path.read_text(encoding="utf-8"))

    def load(self, pid: str) -> Project:
        path = self.dir(pid) / "project.json"
        if not path.exists():
            raise ProjectNotFound(pid)
        return Project.carregar(path)

    def save(self, pid: str, project: Project) -> None:
        with self.lock(pid):
            project.salvar(self.dir(pid) / "project.json")
            info = self.info(pid)
            info.atualizado = _agora()
            self._write_info(info)

    def rename(self, pid: str, nome: str) -> ProjectInfo:
        info = self.info(pid)
        info.nome = nome.strip() or info.nome
        info.atualizado = _agora()
        self._write_info(info)
        return info

    def delete(self, pid: str) -> None:
        path = self.dir(pid)
        if not path.exists():
            raise ProjectNotFound(pid)
        shutil.rmtree(path)

    def _write_info(self, info: ProjectInfo) -> None:
        atomic_write_text(self.dir(info.id) / "info.json", info.model_dump_json(indent=2))

    # ------------------------------------------------------------------ arquivos

    def unique_clip_path(self, pid: str, nome: str) -> Path:
        """Caminho livre em clips/ para um upload (sem sobrescrever outro clipe)."""
        base = Path(nome).name or "clipe.mp4"
        destino = self.clips_dir(pid) / base
        n = 2
        while destino.exists():
            destino = self.clips_dir(pid) / f"{Path(base).stem}_{n}{Path(base).suffix}"
            n += 1
        return destino

    def saida_file(self, pid: str, nome: str) -> Path:
        """Arquivo dentro de saida/ (sem permitir sair da pasta)."""
        saida = self.saida_dir(pid).resolve()
        path = (saida / nome).resolve()
        if saida not in path.parents or not path.is_file():
            raise FileNotFoundError(nome)
        return path
