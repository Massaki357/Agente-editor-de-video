import os

from src.cache import (
    HASH_CHUNK,
    cache_path,
    file_hash,
    read_json_cache,
    write_json_cache,
)


def test_same_content_same_hash_even_in_different_paths(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"x" * 1000)
    b.write_bytes(b"x" * 1000)
    assert file_hash(a) == file_hash(b)
    assert len(file_hash(a)) == 64


def test_change_at_the_end_of_large_file_changes_hash(tmp_path):
    f = tmp_path / "grande.mp4"
    data = bytearray(os.urandom(3 * HASH_CHUNK))
    f.write_bytes(data)
    before = file_hash(f)
    data[-1] ^= 0xFF
    f.write_bytes(data)
    os.utime(f, ns=(1, 1))  # garante mtime diferente mesmo em FS de baixa resolução
    assert file_hash(f) != before


def test_different_size_changes_hash(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x" * 10)
    before = file_hash(f)
    f.write_bytes(b"x" * 11)
    assert file_hash(f) != before


def test_json_cache_roundtrip(isolated_cache):
    assert read_json_cache("probe", "abc") is None
    path = write_json_cache("probe", "abc", {"duracao": 1.5, "texto": "ação"})
    assert path == isolated_cache / "probe" / "abc.json"
    assert read_json_cache("probe", "abc") == {"duracao": 1.5, "texto": "ação"}
    assert [p.name for p in path.parent.iterdir()] == ["abc.json"]  # sem .tmp sobrando


def test_corrupted_cache_is_ignored(isolated_cache):
    path = cache_path("probe", "ruim")
    path.parent.mkdir(parents=True)
    path.write_text("{pela metade", encoding="utf-8")
    assert read_json_cache("probe", "ruim") is None
