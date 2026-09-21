import pytest

import src.clips as clips
from src.clips import (
    ClipProbeError,
    list_clips_from_folder,
    probe_clip,
    project_from_files,
    project_from_folder,
)


def test_natural_order_1_2_10(tmp_path):
    for name in ["10.mp4", "2.mp4", "1.mp4"]:
        (tmp_path / name).touch()
    assert [p.name for p in list_clips_from_folder(tmp_path)] == ["1.mp4", "2.mp4", "10.mp4"]


def test_filters_extensions_hidden_and_dirs(tmp_path):
    for name in ["3.MOV", "11.mkv", "1.mp4", "notas.txt", ".oculto.mp4", "2.avi"]:
        (tmp_path / name).touch()
    (tmp_path / "4.mp4").mkdir()
    assert [p.name for p in list_clips_from_folder(tmp_path)] == ["1.mp4", "3.MOV", "11.mkv"]


def test_natural_order_with_prefixes_and_case(tmp_path):
    for name in ["Take 10.mp4", "take 9.mp4", "Take 1.mp4"]:
        (tmp_path / name).touch()
    got = [p.name for p in list_clips_from_folder(tmp_path)]
    assert got == ["Take 1.mp4", "take 9.mp4", "Take 10.mp4"]


def test_missing_folder_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        list_clips_from_folder(tmp_path / "nao_existe")


def test_parse_rotation_from_side_data():
    info = {
        "format": {"duration": "4.5"},
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30000/1001",
                "side_data_list": [{"rotation": -90}],
            }
        ],
    }
    meta = clips._parse_ffprobe(info, clips.Path("x.mp4"))
    assert (meta.largura, meta.altura, meta.rotacao) == (1080, 1920, 270)
    assert meta.fps == pytest.approx(29.97)
    assert meta.tem_audio is False


def test_parse_ignores_cover_art_stream():
    info = {
        "format": {"duration": "2"},
        "streams": [
            {"codec_type": "video", "width": 1, "height": 1, "disposition": {"attached_pic": 1}},
        ],
    }
    with pytest.raises(ClipProbeError):
        clips._parse_ffprobe(info, clips.Path("x.mp4"))


def test_probe_real_video(video_dir):
    meta = probe_clip(video_dir / "1.mp4")
    assert (meta.largura, meta.altura) == (320, 180)
    assert meta.fps == pytest.approx(30)
    assert meta.duracao == pytest.approx(1.0, abs=0.1)
    assert meta.tem_audio is True
    assert probe_clip(video_dir / "10.mp4").tem_audio is False


def test_probe_uses_cache_on_second_call(video_dir, monkeypatch):
    first = probe_clip(video_dir / "2.mp4")

    def fail(_):
        raise AssertionError("ffprobe não deveria rodar de novo")

    monkeypatch.setattr(clips, "_run_ffprobe", fail)
    assert probe_clip(video_dir / "2.mp4") == first


def test_probe_invalid_file_raises(tmp_path):
    bad = tmp_path / "quebrado.mp4"
    bad.write_bytes(b"isto nao e um video")
    with pytest.raises(ClipProbeError):
        probe_clip(bad)


def test_project_from_folder_orders_and_computes_offsets(video_dir):
    project = project_from_folder(video_dir)
    clipes = project.timeline.clipes
    assert [c.nome for c in clipes] == ["1.mp4", "2.mp4", "10.mp4"]
    assert clipes[0].trechos == [(0.0, clipes[0].meta.duracao)]
    assert clipes[0].offset == 0.0
    assert clipes[1].offset == pytest.approx(clipes[0].meta.duracao)
    assert clipes[2].offset == pytest.approx(clipes[0].meta.duracao + clipes[1].meta.duracao)


def test_project_from_files_keeps_selection_order(video_dir):
    project = project_from_files([video_dir / "10.mp4", video_dir / "1.mp4"])
    assert [c.nome for c in project.timeline.clipes] == ["10.mp4", "1.mp4"]


def test_reorder_then_save_and_load(video_dir, tmp_path):
    project = project_from_folder(video_dir)
    project.timeline.mover_clipe(2, 0)
    loaded = project.carregar(project.salvar(tmp_path / "project.json"))
    assert [c.nome for c in loaded.timeline.clipes] == ["10.mp4", "1.mp4", "2.mp4"]
    assert loaded.timeline.clipes[1].offset == pytest.approx(loaded.timeline.clipes[0].meta.duracao)
