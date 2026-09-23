"""Fila de jobs em segundo plano (um worker: GPU e Whisper não rodam em paralelo).

Cada job tem status, etapa, progresso, log e resultado, e é salvo em
`DATA_DIR/jobs/<id>.json`. O cancelamento é cooperativo: o job verifica o pedido
a cada atualização de progresso (`JobContext.step`).
"""

from __future__ import annotations

import logging
import queue
import threading
import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.cache import atomic_write_text
from src.errors import mensagem_amigavel

log = logging.getLogger(__name__)

MAX_LOG = 300


class JobStatus(StrEnum):
    pendente = "pendente"
    rodando = "rodando"
    concluido = "concluido"
    erro = "erro"
    cancelado = "cancelado"


FINAIS = {JobStatus.concluido, JobStatus.erro, JobStatus.cancelado}


class Job(BaseModel):
    id: str
    projeto_id: str
    tipo: str
    opcoes: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = JobStatus.pendente
    etapa: str | None = None
    progresso: float = 0.0  # 0..1 na etapa atual
    mensagem: str | None = None
    resultado: dict[str, Any] | None = None
    log: list[str] = Field(default_factory=list)
    criado: datetime
    iniciado: datetime | None = None
    terminado: datetime | None = None
    cancelar: bool = False


class JobCancelled(Exception):
    pass


class JobContext:
    """O que um job em execução usa para reportar progresso."""

    def __init__(self, manager: JobManager, job: Job):
        self._manager = manager
        self.job = job

    def step(self, etapa: str, fracao: float) -> None:
        """Atualiza etapa/progresso; lança `JobCancelled` se o cancelamento foi pedido."""
        if self.job.cancelar:
            raise JobCancelled()
        mudou = etapa != self.job.etapa or abs(fracao - self.job.progresso) >= 0.01
        self.job.etapa = etapa
        self.job.progresso = max(0.0, min(1.0, fracao))
        if mudou:
            self._manager._persist(self.job)


Runner = Callable[[Job, JobContext], dict[str, Any]]


class _JobLogHandler(logging.Handler):
    """Copia para o job os logs emitidos pela thread do worker."""

    def __init__(self, job: Job, thread_id: int):
        super().__init__(logging.INFO)
        self.job = job
        self.thread_id = thread_id
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.thread_id:
            return
        self.job.log.append(self.format(record))
        del self.job.log[:-MAX_LOG]


class JobManager:
    def __init__(self, jobs_dir: Path, runner: Runner):
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self._jobs: dict[str, Job] = {}
        self._fila: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._load()

    # ------------------------------------------------------------------ ciclo de vida

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, name="jobs", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        for job in self.list():
            if job.status in (JobStatus.pendente, JobStatus.rodando):
                job.cancelar = True
        self._fila.put(None)
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    # ------------------------------------------------------------------ API

    def submit(self, projeto_id: str, tipo: str, opcoes: dict[str, Any] | None = None) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            projeto_id=projeto_id,
            tipo=tipo,
            opcoes=opcoes or {},
            criado=datetime.now(UTC),
        )
        with self._lock:
            self._jobs[job.id] = job
        self._persist(job)
        self._fila.put(job.id)
        return job

    def get(self, jid: str) -> Job:
        return self._jobs[jid]

    def list(self, projeto_id: str | None = None) -> list[Job]:
        jobs = [j for j in self._jobs.values() if projeto_id in (None, j.projeto_id)]
        return sorted(jobs, key=lambda j: j.criado, reverse=True)

    def active(self, projeto_id: str) -> Job | None:
        """Job pendente ou rodando do projeto (o projeto não pode mudar enquanto isso)."""
        return next(
            (j for j in self.list(projeto_id) if j.status not in FINAIS),
            None,
        )

    def cancel(self, jid: str) -> Job:
        job = self._jobs[jid]
        if job.status == JobStatus.pendente:
            self._finish(job, JobStatus.cancelado, "cancelado antes de começar")
        elif job.status == JobStatus.rodando:
            job.cancelar = True
            job.mensagem = "cancelando..."
            self._persist(job)
        return job

    def forget_project(self, projeto_id: str) -> None:
        with self._lock:
            for job in list(self._jobs.values()):
                if job.projeto_id == projeto_id and job.status in FINAIS:
                    del self._jobs[job.id]
                    (self.jobs_dir / f"{job.id}.json").unlink(missing_ok=True)

    # ------------------------------------------------------------------ interno

    def _worker(self) -> None:
        while True:
            jid = self._fila.get()
            if jid is None:
                return
            job = self._jobs.get(jid)
            if job is None or job.status != JobStatus.pendente:
                continue
            self._run(job)

    def _run(self, job: Job) -> None:
        job.status = JobStatus.rodando
        job.iniciado = datetime.now(UTC)
        self._persist(job)
        handler = _JobLogHandler(job, threading.get_ident())
        logging.getLogger().addHandler(handler)
        try:
            resultado = self.runner(job, JobContext(self, job))
            job.resultado = resultado
            job.progresso = 1.0
            self._finish(job, JobStatus.concluido, None)
        except JobCancelled:
            self._finish(job, JobStatus.cancelado, "cancelado")
        except Exception as exc:  # o worker nunca morre por causa de um job
            job.log.append(traceback.format_exc(limit=5))
            log.warning("Job %s (%s) falhou: %s", job.id, job.tipo, exc)
            self._finish(job, JobStatus.erro, mensagem_amigavel(exc))
        finally:
            logging.getLogger().removeHandler(handler)

    def _finish(self, job: Job, status: JobStatus, mensagem: str | None) -> None:
        job.status = status
        job.mensagem = mensagem
        job.terminado = datetime.now(UTC)
        self._persist(job)

    def _persist(self, job: Job) -> None:
        try:
            atomic_write_text(self.jobs_dir / f"{job.id}.json", job.model_dump_json(indent=2))
        except OSError as exc:
            log.warning("Não foi possível salvar o job %s: %s", job.id, exc)

    def _load(self) -> None:
        """Recarrega jobs antigos; os que estavam em andamento viram erro (API reiniciou)."""
        for path in self.jobs_dir.glob("*.json"):
            try:
                job = Job.model_validate_json(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if job.status not in FINAIS:
                job.status = JobStatus.erro
                job.mensagem = "interrompido: a API foi reiniciada"
                job.terminado = datetime.now(UTC)
                self._persist(job)
            self._jobs[job.id] = job
