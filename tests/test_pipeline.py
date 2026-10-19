"""pipeline 模块测试：验证 CLI/GUI 共用的流水线逻辑。"""
from pathlib import Path

from mutagen.id3 import ID3, TIT2, TPE1, Encoding

from mp3organizer.pipeline import run_pipeline

FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413  # 有效 MPEG 帧头 + 空数据


def _garbled(text: str) -> str:
    return text.encode("gbk").decode("latin1")


def _make_mp3(path: Path, title_garbled: str, artist_garbled: str) -> None:
    tag = ID3()
    tag.add(TIT2(encoding=Encoding.LATIN1, text=[title_garbled]))
    tag.add(TPE1(encoding=Encoding.LATIN1, text=[artist_garbled]))
    tag.save(str(path))
    with open(path, "ab") as f:
        f.write(FRAME * 20)


def test_pipeline_dry_run_changes_nothing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_mp3(src / "a.mp3", _garbled("夜曲"), _garbled("周杰伦"))
    before = (src / "a.mp3").read_bytes()
    seen_phases = set()

    result = run_pipeline(
        src, tmp_path / "lib",
        dry_run=True, do_cover=False,
        mp3val_exe=None, ffmpeg_exe=None,  # 跳过流检查（无音频实数据）
        progress=lambda i, t, ph, msg: seen_phases.add(ph),
    )
    assert result["total"] == 1
    assert (src / "a.mp3").read_bytes() == before  # 试运行不改文件
    rec = result["records"][0]
    assert rec["tag_status"] == "fixed"
    assert rec["integrity_status"] == "skipped"  # 工具缺失
    assert rec["cover_status"] == "skipped"
    assert seen_phases >= {"scan", "tags", "integrity", "organize", "done"}


def test_pipeline_real_run_organizes(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_mp3(src / "a.mp3", _garbled("夜曲"), _garbled("周杰伦"))
    result = run_pipeline(
        src, tmp_path / "lib",
        do_cover=False, mp3val_exe=None, ffmpeg_exe=None,
        backup_dir=tmp_path / "bak",
    )
    dest = tmp_path / "lib" / "未知类型" / "周杰伦" / "夜曲.mp3"
    assert dest.exists()
    reread = ID3(str(dest))
    assert reread["TIT2"].text == ["夜曲"]
    assert (tmp_path / "bak").exists()


def test_pipeline_quarantines_broken(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    bad = src / "bad.mp3"
    bad.write_bytes(b"\xff\xfb\x90")  # 残缺帧头，mutagen 无法解析
    result = run_pipeline(
        src, tmp_path / "lib",
        do_cover=False, mp3val_exe=None, ffmpeg_exe=None,
        quarantine_dir=tmp_path / "q",
    )
    rec = result["records"][0]
    assert rec["integrity_status"] == "unreadable"
    assert (tmp_path / "q" / "bad[损坏].mp3").exists()


def test_pipeline_guess_from_filename(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "王菲 - 红豆.mp3").write_bytes(FRAME * 20)  # 无标签
    result = run_pipeline(
        src, tmp_path / "lib",
        do_cover=False, mp3val_exe=None, ffmpeg_exe=None,
        guess_from_filename=True,
    )
    rec = result["records"][0]
    assert rec["tag_status"] == "guessed"
    assert (tmp_path / "lib" / "未知类型" / "王菲" / "红豆.mp3").exists()


def test_pipeline_requires_library_when_organizing(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        run_pipeline(tmp_path, None, do_organize=True)


def test_pipeline_steps_can_be_disabled(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_mp3(src / "a.mp3", _garbled("夜曲"), _garbled("周杰伦"))
    result = run_pipeline(
        src, None, do_tags=False, do_check=False,
        do_organize=False, do_cover=False,
    )
    rec = result["records"][0]
    assert rec["tag_status"] == "skipped"
    assert rec["integrity_status"] == "skipped"
    assert rec["cover_status"] == "skipped"
    assert rec["new_path"] == ""
