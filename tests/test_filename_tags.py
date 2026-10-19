"""filename_tags 模块测试：各种常见命名格式。"""
from pathlib import Path

from mp3organizer.filename_tags import guess_from_filename


def g(name: str) -> dict:
    return guess_from_filename(Path(f"/m/{name}"))


def test_artist_dash_title():
    r = g("周杰伦 - 夜曲.mp3")
    assert (r["artist"], r["title"]) == ("周杰伦", "夜曲")


def test_artist_dash_title_no_spaces():
    r = g("王菲-红豆.mp3")
    assert (r["artist"], r["title"]) == ("王菲", "红豆")


def test_title_only():
    r = g("夜曲.mp3")
    assert r["title"] == "夜曲" and r["artist"] == ""


def test_track_number_prefix():
    r = g("03. 青花瓷 - 周杰伦.mp3")
    assert r["track"] == "3"
    # 注意：两段式按“演唱者 - 标题”理解，这里是反例，仅验证解析不崩
    assert r["title"]


def test_track_number_artist_first():
    r = g("001_周杰伦 - 夜曲.mp3")
    assert r["track"] == "1"
    assert (r["artist"], r["title"]) == ("周杰伦", "夜曲")


def test_three_parts_artist_album_title():
    r = g("周杰伦 - 十一月的萧邦 - 夜曲.mp3")
    assert (r["artist"], r["album"], r["title"]) == ("周杰伦", "十一月的萧邦", "夜曲")


def test_junk_bitrate_stripped():
    r = g("[320K]周杰伦 - 夜曲.mp3")
    assert (r["artist"], r["title"]) == ("周杰伦", "夜曲")


def test_junk_suffix_stripped():
    r = g("周杰伦 - 夜曲 (320kbps).mp3")
    assert (r["artist"], r["title"]) == ("周杰伦", "夜曲")


def test_leading_bracket_dropped():
    r = g("[华语]周杰伦 - 夜曲.mp3")
    assert (r["artist"], r["title"]) == ("周杰伦", "夜曲")


def test_garbled_filename_also_fixed():
    garbled = "周杰伦 - 夜曲".encode("gbk").decode("latin1")
    r = g(garbled + ".mp3")
    assert (r["artist"], r["title"]) == ("周杰伦", "夜曲")


def test_empty_after_clean():
    r = g("[320K].mp3")
    assert r["title"] == "" and r["artist"] == ""
