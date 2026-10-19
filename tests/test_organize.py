"""organize 模块测试。"""
from pathlib import Path

from mp3organizer.organize import (
    normalize_genre,
    sanitize,
    target_path,
    organize_file,
    UNKNOWN_GENRE,
    UNKNOWN_ARTIST,
)


def test_normalize_genre_number():
    assert normalize_genre("(13)") == "Pop"
    assert normalize_genre("(13)Pop") == "Pop"


def test_normalize_genre_empty():
    assert normalize_genre("") == UNKNOWN_GENRE
    assert normalize_genre(None) == UNKNOWN_GENRE


def test_normalize_genre_text_passthrough():
    assert normalize_genre("摇滚") == "摇滚"


def test_sanitize_illegal_chars():
    assert sanitize('a/b:c*d?e"f<g>h|i') == "a_b_c_d_e_f_g_h_i"


def test_sanitize_trailing_dots():
    assert sanitize("abc...") == "abc"


def test_target_path_with_album():
    info = {"path": "/m/x.mp3", "title": "夜曲", "artist": "周杰伦",
            "album": "十一月的萧邦", "genre": "Pop"}
    got = target_path("/lib", info)
    assert got == Path("/lib/Pop/周杰伦/周杰伦 - 十一月的萧邦/夜曲.mp3")


def test_target_path_without_album():
    info = {"path": "/m/x.mp3", "title": "夜曲", "artist": "周杰伦",
            "album": "", "genre": ""}
    got = target_path("/lib", info)
    assert got == Path(f"/lib/{UNKNOWN_GENRE}/周杰伦/夜曲.mp3")


def test_target_path_missing_artist():
    info = {"path": "/m/x.mp3", "title": "夜曲", "artist": "", "album": "", "genre": ""}
    got = target_path("/lib", info)
    assert UNKNOWN_ARTIST in str(got)


def test_organize_file_moves(tmp_path):
    src = tmp_path / "src" / "a.mp3"
    src.parent.mkdir()
    src.write_bytes(b"fake")
    info = {"path": str(src), "title": "夜曲", "artist": "周杰伦", "album": "", "genre": "Pop"}
    res = organize_file(src, tmp_path / "lib", info)
    assert res["status"] == "moved"
    assert Path(res["dest"]).exists()
    assert not src.exists()


def test_organize_file_collision_never_overwrites(tmp_path):
    lib = tmp_path / "lib"
    info = {"path": "x.mp3", "title": "夜曲", "artist": "周杰伦", "album": "", "genre": "Pop"}
    for i in range(2):
        src = tmp_path / f"s{i}.mp3"
        src.write_bytes(f"fake{i}".encode())
        organize_file(src, lib, {**info, "path": str(src)})
    dests = sorted((lib / "Pop" / "周杰伦").glob("*.mp3"))
    # 注意：按 Unicode 码点排序，空格(U+0020) < 点号(U+002E)
    assert [d.name for d in dests] == ["夜曲 (1).mp3", "夜曲.mp3"]
