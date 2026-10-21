"""scanner 模块测试：递归扫描与标签读取兜底。"""
from mp3organizer.scanner import find_mp3_files, read_info


def test_find_mp3_files_recurses(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.MP3").write_bytes(b"x")
    (tmp_path / "sub" / "deep").mkdir()
    (tmp_path / "sub" / "deep" / "c.mp3").write_bytes(b"x")
    (tmp_path / "note.txt").write_bytes(b"x")
    got = find_mp3_files(tmp_path)
    assert sorted(p.name for p in got) == ["a.mp3", "b.MP3", "c.mp3"]


def test_read_info_missing_file_returns_empty(tmp_path):
    info = read_info(tmp_path / "nope.mp3")
    assert info["title"] == "" and info["artist"] == ""
