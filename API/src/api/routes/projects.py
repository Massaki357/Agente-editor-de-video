"""Projetos e clipes."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

from src.api.deps import Jobs, Store, ensure_idle, ensure_project
from src.api.schemas import (
    ClipOut,
    ImportFolder,
    ProjectCreate,
    ProjectOut,
    ProjectRename,
    ProjectSummary,
    Reorder,
    RostoOut,
    TranscricaoOut,
)
from src.api.store import ProjectStore
from src.api.tasks import VIDEO_FINAL, debug_video_name
from src.cache import file_hash
from src.clips import VIDEO_EXTENSIONS, ClipProbeError, clip_from_file, list_clips_from_folder
from src.config import REPO_ROOT
from src.face import cached_track
from src.project import Clip, Project
from src.transcribe import cached_transcription
from src.video.stabilize import cached_video_file

router = APIRouter(prefix="/projects", tags=["projetos"])


# --------------------------------------------------------------------------- montagem


def _base(pid: str) -> str:
    return f"/api/projects/{pid}"


def _video_final_url(store: ProjectStore, pid: str) -> str | None:
    path = store.dir(pid) / "saida" / VIDEO_FINAL
    if not path.exists():
        return None
    return f"{_base(pid)}/files/{VIDEO_FINAL}?v={int(path.stat().st_mtime)}"


def _face_source(project: Project, path: Path) -> Path | None:
    if not project.estabilizar:
        return path
    try:
        return cached_video_file(path, project.suavizacao_estabilizacao)
    except RuntimeError:  # sem FFmpeg: o doctor informa a causa, a listagem continua disponível
        return None


def _clip_out(pid: str, i: int, clip: Clip, project: Project) -> ClipOut:
    meta = clip.meta
    path = Path(clip.arquivo)
    existe = path.exists()
    # `v` = hash do arquivo: a URL muda quando o clipe na posição muda (sem cache errado)
    versao = file_hash(path)[:12] if existe else "ausente"
    return ClipOut(
        indice=i,
        nome=clip.nome,
        arquivo=clip.arquivo,
        duracao=meta.duracao if meta else None,
        largura=meta.largura if meta else None,
        altura=meta.altura if meta else None,
        fps=meta.fps if meta else None,
        tem_audio=meta.tem_audio if meta else None,
        vfr=meta.vfr if meta else False,
        hdr=meta.hdr if meta else False,
        trechos=clip.trechos,
        offset=clip.offset,
        duracao_mantida=clip.duracao_mantida,
        transcrito=existe and cached_transcription(path) is not None,
        rosto=existe
        and (face_path := _face_source(project, path)) is not None
        and cached_track(face_path) is not None,
        video_url=f"{_base(pid)}/clips/{i}/video?v={versao}",
        thumbnail_url=f"{_base(pid)}/clips/{i}/thumbnail?v={versao}",
    )


def project_out(store: ProjectStore, jobs, pid: str) -> ProjectOut:
    info = store.info(pid)
    project = store.load(pid)
    saida = store.dir(pid) / "saida"
    arquivos = sorted(p.name for p in saida.glob("*.mp4")) if saida.exists() else []
    ativo = jobs.active(pid)
    return ProjectOut(
        **info.model_dump(),
        n_clipes=len(project.timeline.clipes),
        video_final_url=_video_final_url(store, pid),
        clipes=[_clip_out(pid, i, c, project) for i, c in enumerate(project.timeline.clipes)],
        estabilizar=project.estabilizar,
        suavizacao_estabilizacao=project.suavizacao_estabilizacao,
        broll=project.broll,
        broll_intervalo_min=project.broll_intervalo_min,
        broll_transition=project.broll_transition,
        legendas_continuas=project.legendas_continuas,
        legendas_destaque=project.legendas_destaque,
        estilo_destaque=project.estilo_destaque,
        duracao_total=project.timeline.duracao_total,
        arquivos=arquivos,
        job_ativo=ativo.id if ativo else None,
    )


def _clip_path(store: ProjectStore, pid: str, indice: int) -> Path:
    """Arquivo do clipe; 404 se ele foi apagado ou movido depois de importado."""
    path = Path(_clip(store, pid, indice).arquivo)
    if not path.exists():
        raise HTTPException(404, f"arquivo do clipe não encontrado: {path}")
    return path


def _clip(store: ProjectStore, pid: str, indice: int) -> Clip:
    ensure_project(store, pid)
    clipes = store.load(pid).timeline.clipes
    if not 0 <= indice < len(clipes):
        raise HTTPException(404, f"clipe {indice} não existe")
    return clipes[indice]


# --------------------------------------------------------------------------- projetos


@router.get("", response_model=list[ProjectSummary])
def list_projects(store: Store) -> list[ProjectSummary]:
    resumo = []
    for info in store.list():
        n = len(store.load(info.id).timeline.clipes)
        resumo.append(
            ProjectSummary(
                **info.model_dump(), n_clipes=n, video_final_url=_video_final_url(store, info.id)
            )
        )
    return resumo


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, store: Store, jobs: Jobs) -> ProjectOut:
    info = store.create(body.nome)
    return project_out(store, jobs, info.id)


@router.get("/{pid}", response_model=ProjectOut)
def get_project(pid: str, store: Store, jobs: Jobs) -> ProjectOut:
    ensure_project(store, pid)
    return project_out(store, jobs, pid)


@router.patch("/{pid}", response_model=ProjectOut)
def rename_project(pid: str, body: ProjectRename, store: Store, jobs: Jobs) -> ProjectOut:
    ensure_project(store, pid)
    store.rename(pid, body.nome)
    return project_out(store, jobs, pid)


@router.delete("/{pid}", status_code=204)
def delete_project(pid: str, store: Store, jobs: Jobs) -> None:
    ensure_project(store, pid)
    with store.lock(pid):
        ensure_idle(jobs, pid)
        store.delete(pid)
    jobs.forget_project(pid)


# --------------------------------------------------------------------------- clipes


@router.post("/{pid}/clips", response_model=ProjectOut)
def upload_clips(pid: str, files: list[UploadFile], store: Store, jobs: Jobs) -> ProjectOut:
    """Envia vídeos; entram no fim da timeline, na ordem em que foram enviados."""
    ensure_project(store, pid)
    ensure_idle(jobs, pid)
    formatos = ", ".join(sorted(VIDEO_EXTENSIONS))
    for f in files:  # valida tudo antes de gravar qualquer coisa
        if Path(f.filename or "").suffix.lower() not in VIDEO_EXTENSIONS:
            raise HTTPException(422, f"{f.filename}: formato não suportado (use {formatos})")
    with store.lock(pid):
        ensure_idle(jobs, pid)  # de novo, sob o lock
        project = store.load(pid)
        gravados: list[Path] = []
        try:
            for f in files:
                destino = store.unique_clip_path(pid, f.filename or "clipe.mp4")
                with destino.open("wb") as out:
                    shutil.copyfileobj(f.file, out)
                gravados.append(destino)
                try:
                    project.timeline.adicionar_clipe(clip_from_file(destino))
                except ClipProbeError as exc:
                    raise HTTPException(422, str(exc)) from None
        except BaseException:
            for path in gravados:  # tudo ou nada: sem arquivos órfãos
                path.unlink(missing_ok=True)
            raise
        store.save(pid, project)
    return project_out(store, jobs, pid)


@router.post("/{pid}/clips/import", response_model=ProjectOut)
def import_folder(pid: str, body: ImportFolder, store: Store, jobs: Jobs) -> ProjectOut:
    """Adiciona os vídeos de uma pasta local em ordem natural (1, 2, 10). Não copia."""
    ensure_project(store, pid)
    ensure_idle(jobs, pid)
    try:
        pasta = Path(body.pasta).expanduser()
        if not pasta.is_absolute():  # "samples" = pasta samples/ da raiz do repositório
            pasta = REPO_ROOT / pasta
        arquivos = list_clips_from_folder(pasta)
    except NotADirectoryError:
        raise HTTPException(422, f"pasta não encontrada: {body.pasta}") from None
    if not arquivos:
        raise HTTPException(422, f"nenhum vídeo ({', '.join(sorted(VIDEO_EXTENSIONS))}) na pasta")
    with store.lock(pid):
        ensure_idle(jobs, pid)  # de novo, sob o lock
        project = store.load(pid)
        for path in arquivos:
            try:
                project.timeline.adicionar_clipe(clip_from_file(path))
            except ClipProbeError as exc:
                raise HTTPException(422, str(exc)) from None
        store.save(pid, project)
    return project_out(store, jobs, pid)


@router.put("/{pid}/clips/order", response_model=ProjectOut)
def reorder_clips(pid: str, body: Reorder, store: Store, jobs: Jobs) -> ProjectOut:
    ensure_project(store, pid)
    ensure_idle(jobs, pid)
    with store.lock(pid):
        ensure_idle(jobs, pid)  # de novo, sob o lock
        project = store.load(pid)
        try:
            project.timeline.reordenar(body.ordem)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        store.save(pid, project)
    return project_out(store, jobs, pid)


@router.delete("/{pid}/clips/{indice}", response_model=ProjectOut)
def remove_clip(pid: str, indice: int, store: Store, jobs: Jobs) -> ProjectOut:
    ensure_project(store, pid)
    ensure_idle(jobs, pid)
    with store.lock(pid):
        ensure_idle(jobs, pid)  # de novo, sob o lock
        project = store.load(pid)
        if not 0 <= indice < len(project.timeline.clipes):
            raise HTTPException(404, f"clipe {indice} não existe")
        removido = project.timeline.remover_clipe(indice)
        store.save(pid, project)
    # upload (dentro de clips/) é apagado; arquivo importado de outra pasta fica
    path = Path(removido.arquivo)
    if store.clips_dir(pid).resolve() in path.resolve().parents:
        path.unlink(missing_ok=True)
    return project_out(store, jobs, pid)


@router.get("/{pid}/clips/{indice}/video")
def clip_video(pid: str, indice: int, store: Store) -> FileResponse:
    return FileResponse(_clip_path(store, pid, indice))


@router.get("/{pid}/clips/{indice}/thumbnail")
def clip_thumbnail(pid: str, indice: int, store: Store) -> FileResponse:
    clip = _clip(store, pid, indice)
    path = _clip_path(store, pid, indice)
    thumbs = store.saida_dir(pid) / "thumbs"
    thumbs.mkdir(exist_ok=True)
    destino = thumbs / f"{file_hash(path)[:16]}.jpg"
    if not destino.exists():
        t = min(1.0, (clip.meta.duracao / 2) if clip.meta else 0.0)
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-ss", str(t)]
        cmd += ["-i", str(path), "-frames:v", "1", "-vf", "scale=320:-2", str(destino)]
        if subprocess.run(cmd, capture_output=True).returncode != 0 or not destino.exists():
            # arquivo corrompido ou codec exótico: não é erro do servidor
            raise HTTPException(422, f"não foi possível ler um frame de {Path(path).name}")
    return FileResponse(destino, media_type="image/jpeg")


@router.get("/{pid}/clips/{indice}/transcricao", response_model=TranscricaoOut)
def clip_transcription(pid: str, indice: int, store: Store) -> TranscricaoOut:
    """Transcrição em cache. Para transcrever, crie um job `transcrever`."""
    t = cached_transcription(_clip_path(store, pid, indice))
    if t is None:
        raise HTTPException(404, "clipe ainda não transcrito (rode o job 'transcrever')")
    return TranscricaoOut(
        modelo=t.modelo,
        duracao=t.duracao,
        texto=t.texto,
        palavras=[p.model_dump() for p in t.palavras],
    )


@router.get("/{pid}/clips/{indice}/rosto", response_model=RostoOut)
def clip_face(pid: str, indice: int, store: Store) -> RostoOut:
    """Resumo do rastreio de rosto em cache. Para rastrear, crie um job `rosto`."""
    path = _clip_path(store, pid, indice)
    project = store.load(pid)
    fonte = _face_source(project, path)
    track = cached_track(fonte) if fonte is not None else None
    if track is None:
        raise HTTPException(404, "rosto ainda não rastreado (rode o job 'rosto')")
    debug = store.dir(pid) / "saida" / debug_video_name(fonte)
    return RostoOut(
        fps=track.fps,
        largura=track.largura,
        altura=track.altura,
        n_frames=track.n_frames,
        cobertura=track.cobertura,
        debug_url=f"{_base(pid)}/files/{debug.name}" if debug.exists() else None,
    )


# --------------------------------------------------------------------------- saída


@router.get("/{pid}/files/{nome}")
def output_file(pid: str, nome: str, store: Store) -> FileResponse:
    """Arquivos gerados (vídeo final, vídeos de debug)."""
    ensure_project(store, pid)
    try:
        path = store.saida_file(pid, nome)
    except FileNotFoundError:
        raise HTTPException(404, f"{nome} não existe") from None
    return FileResponse(path, filename=nome, content_disposition_type="inline")
