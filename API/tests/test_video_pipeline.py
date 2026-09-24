"""Parte 2, Etapa 4: estabilização por clipe no rastreio e no render."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import src.pipeline as pipeline
from src.api.jobs import Job
from src.clips import clip_from_file
from src.pipeline import PipelineOptions, project_video_estabilizado, render_project
from src.project import Project, Timeline
from src.video.stabilize import cached_video

from .conftest import make_video, requires_ffmpeg


def _project(*paths: Path) -> Project:
    timeline = Timeline(clipes=[clip_from_file(path) for path in paths])
    timeline.recalcular_offsets()
    return Project(timeline=timeline)


def test_option_defaults_and_validation():
    options = PipelineOptions()
    assert options.estabilizar is False
    assert options.suavizacao_estabilizacao == "medio"
    with pytest.raises(ValueError):
        PipelineOptions(suavizacao_estabilizacao="invalido")


def test_new_projects_and_jobs_inherit_env_smoothing(monkeypatch):
    from src.config import Settings

    settings = Settings(stabilize_smoothing="forte")
    monkeypatch.setattr("src.project.get_settings", lambda: settings)
    monkeypatch.setattr("src.pipeline.get_settings", lambda: settings)
    assert Project().suavizacao_estabilizacao == "forte"
    assert PipelineOptions().suavizacao_estabilizacao == "forte"


@requires_ffmpeg
def test_stabilization_uses_a_distinct_cached_file_for_each_clip(tmp_path, monkeypatch):
    from src.config import Settings

    paths = [make_video(tmp_path / f"{i}.mp4", duration=0.8 + i * 0.2) for i in (1, 2)]
    project = _project(*paths)
    for clip in project.timeline.clipes:
        clip.trechos = [(0.0, 0.5)]
    settings = Settings(cache_dir=tmp_path / "cache")
    monkeypatch.setattr(
        pipeline,
        "cached_video",
        lambda path, smoothing, on_progress=None: cached_video(
            path, smoothing, settings=settings, on_progress=on_progress
        ),
    )
    options = PipelineOptions(estabilizar=True, suavizacao_estabilizacao="leve")
    prepared = project_video_estabilizado(project, options)
    stabilized = [Path(clip.arquivo) for clip in prepared.timeline.clipes]
    assert all(path.is_file() for path in stabilized)
    assert len(set(stabilized)) == 2
    assert [Path(c.arquivo) for c in project.timeline.clipes] == paths

    modified = [path.stat().st_mtime_ns for path in stabilized]
    again = project_video_estabilizado(project, options)
    assert [Path(c.arquivo) for c in again.timeline.clipes] == stabilized
    assert [path.stat().st_mtime_ns for path in stabilized] == modified
    stronger = project_video_estabilizado(
        project, PipelineOptions(estabilizar=True, suavizacao_estabilizacao="forte")
    )
    assert [Path(c.arquivo) for c in stronger.timeline.clipes] != stabilized


@requires_ffmpeg
def test_original_stays_for_captions_and_stabilized_video_goes_to_face_and_render(
    tmp_path, monkeypatch
):
    video = make_video(tmp_path / "source.mp4", duration=1.0)
    stabilized = make_video(tmp_path / "stabilized.mp4", duration=1.0)
    project = _project(video)
    paths = {}
    monkeypatch.setattr(pipeline, "cached_video", lambda *a, **kw: stabilized)

    def face_tracks(prepared, on_step):
        paths["face"] = Path(prepared.timeline.clipes[0].arquivo)
        return {}

    def captions(original, *args):
        paths["captions"] = Path(original.timeline.clipes[0].arquivo)
        return None

    def render(timeline, output, **kwargs):
        paths["render"] = Path(timeline.clipes[0].arquivo)

    monkeypatch.setattr(pipeline, "face_tracks", face_tracks)
    monkeypatch.setattr(pipeline, "make_captions", captions)
    monkeypatch.setattr("src.render.render_timeline", render)
    render_project(
        project,
        tmp_path / "final.mp4",
        PipelineOptions(
            estabilizar=True,
            cortes=False,
            reenquadrar=True,
            legendas=True,
            imagens=False,
            zooms=False,
        ),
    )
    assert paths == {"face": stabilized, "captions": video, "render": stabilized}
    assert Path(project.timeline.clipes[0].arquivo) == video


@requires_ffmpeg
def test_render_works_with_option_off_and_on(tmp_path):
    video = make_video(tmp_path / "source.mp4", duration=1.0)
    project = _project(video)
    base = dict(cortes=False, reenquadrar=False, legendas=False, imagens=False, zooms=False)
    for stabilize in (False, True):
        dest = tmp_path / f"final_{stabilize}.mp4"
        result = render_project(project, dest, PipelineOptions(estabilizar=stabilize, **base))
        assert dest.is_file() and dest.stat().st_size > 0
        assert ("estabilização" in result.tempos) is stabilize
        streams = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(dest),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "video" in streams and "audio" in streams
    assert Path(project.timeline.clipes[0].arquivo) == video


@requires_ffmpeg
def test_face_job_tracks_and_draws_on_stabilized_video(tmp_path, monkeypatch):
    import src.api.tasks as tasks
    import src.video.stabilize as stabilize

    original = make_video(tmp_path / "original.mp4")
    stabilized = make_video(tmp_path / "stabilized.mp4")
    project = _project(original)
    paths = []

    class Store:
        def load(self, pid):
            return project

        def saida_dir(self, pid):
            return tmp_path

    class Context:
        def step(self, etapa, fracao):
            pass

    class Track:
        cobertura = 1.0

    monkeypatch.setattr(stabilize, "cached_video", lambda *a, **kw: stabilized)

    def track(path, *args, **kwargs):
        paths.append(Path(path))
        return Track()

    def debug(path, *args, **kwargs):
        paths.append(Path(path))

    monkeypatch.setattr(tasks, "track_faces", track)
    monkeypatch.setattr(tasks, "render_debug", debug)
    job = Job(
        id="test",
        projeto_id="project",
        tipo="rosto",
        criado=datetime.now(UTC),
        opcoes={"estabilizar": True, "suavizacao_estabilizacao": "forte"},
    )
    tasks.rosto(Store(), job, Context())
    assert paths == [stabilized, stabilized]
    assert Path(project.timeline.clipes[0].arquivo) == original


@requires_ffmpeg
def test_render_also_works_with_automatic_opencv_fallback(tmp_path, monkeypatch):
    import src.video.stabilize as stabilize

    original = make_video(tmp_path / "original.mp4", duration=1.0)
    project = _project(original)
    monkeypatch.setattr(stabilize, "selecionar_metodo", lambda metodo="auto": "opencv")
    output = tmp_path / "fallback_final.mp4"
    render_project(
        project,
        output,
        PipelineOptions(
            estabilizar=True,
            cortes=False,
            reenquadrar=False,
            legendas=False,
            imagens=False,
            zooms=False,
        ),
    )
    assert output.is_file() and output.stat().st_size > 0
    assert Path(project.timeline.clipes[0].arquivo) == original
