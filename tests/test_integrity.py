"""integrity 模块测试：用注入的假检查器验证流程逻辑。"""
from pathlib import Path

from mp3organizer.integrity import check_file, quarantine


def _runners(mutagen_ok=True, mp3val=None, ffmpeg=None):
    return {
        "mutagen": lambda p: (mutagen_ok, "" if mutagen_ok else "boom"),
        "mp3val": (lambda p: mp3val) if mp3val else None,
        "ffmpeg": (lambda p: ffmpeg) if ffmpeg else None,
    }


def test_check_ok(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    r = check_file(p, mp3val_exe="X", ffmpeg_exe="Y",
                   runners=_runners(mp3val=("ok", []), ffmpeg=("ok", [])))
    assert r.status == "ok"


def test_check_mp3val_fixed(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    r = check_file(p, mp3val_exe="X", ffmpeg_exe="Y",
                   runners=_runners(mp3val=("fixed", ["FIXED: rebuilt header"]),
                                    ffmpeg=("ok", [])))
    assert r.status == "fixed"
    assert any("rebuilt" in d for d in r.details)


def test_check_ffmpeg_broken_wins(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    r = check_file(p, mp3val_exe="X", ffmpeg_exe="Y",
                   runners=_runners(mp3val=("fixed", []),
                                    ffmpeg=("broken", ["[mp3] Header missing"]) ))
    assert r.status == "broken"


def test_check_unreadable_short_circuits(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    r = check_file(p, runners=_runners(mutagen_ok=False))
    assert r.status == "unreadable"


def test_check_no_tools_skipped(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    r = check_file(p, mp3val_exe=None, ffmpeg_exe=None,
                   runners={"mutagen": lambda p: (True, "")})
    assert r.status == "skipped"


def test_quarantine_renames_and_moves(tmp_path):
    src = tmp_path / "song.mp3"
    src.write_bytes(b"fake")
    dest = quarantine(src, tmp_path / "q")
    assert dest.name == "song[损坏].mp3"
    assert dest.exists()
    assert not src.exists()
