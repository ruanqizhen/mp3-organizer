"""cli 回归测试：GBK 控制台下打印乱码标签原文不崩溃。"""
import io
import sys
from pathlib import Path

from mutagen.id3 import ID3, TIT2, TPE1, Encoding

from mp3organizer.cli import main, _ensure_safe_console_output


def _make_garbled_mp3(path: Path) -> None:
    tag = ID3()
    tag.add(TIT2(encoding=Encoding.LATIN1, text=["夜曲".encode("gbk").decode("latin1")]))
    tag.add(TPE1(encoding=Encoding.LATIN1, text=["周杰伦".encode("gbk").decode("latin1")]))
    tag.save(str(path))


def test_fix_tags_gbk_console_does_not_crash(tmp_path, monkeypatch):
    """模拟 GBK/strict 控制台：乱码原文降级显示，修复后的中文正常显示。"""
    _make_garbled_mp3(tmp_path / "a.mp3")
    buf = io.BytesIO()
    gbk_stdout = io.TextIOWrapper(buf, encoding="gbk", errors="strict")
    monkeypatch.setattr(sys, "stdout", gbk_stdout)
    assert main(["fix-tags", str(tmp_path), "--dry-run"]) == 0
    gbk_stdout.flush()
    out = buf.getvalue().decode("gbk", errors="replace")
    assert "夜曲" in out
    assert "周杰伦" in out


def test_safe_console_tolerates_plain_streams(monkeypatch):
    """无 reconfigure 的输出流（如 StringIO）不导致启动失败。"""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    _ensure_safe_console_output()


def _make_tagged_mp3(path, title, artist):
    from mutagen.id3 import ID3, TIT2, TPE1, Encoding
    tag = ID3()
    tag.add(TIT2(encoding=Encoding.UTF8, text=[title]))
    tag.add(TPE1(encoding=Encoding.UTF8, text=[artist]))
    tag.save(str(path))


def test_organize_cli_personal_default(tmp_path):
    _make_tagged_mp3(tmp_path / "a.mp3", "Hello", "Adele")
    assert main(["organize", str(tmp_path), str(tmp_path / "lib")]) == 0
    assert (tmp_path / "lib" / "英语" / "Adele - Hello.mp3").exists()


def test_organize_cli_classic_flag(tmp_path):
    _make_tagged_mp3(tmp_path / "a.mp3", "Hello", "Adele")
    assert main(["organize", str(tmp_path), str(tmp_path / "lib"),
                 "--template", "classic"]) == 0
    assert (tmp_path / "lib" / "未知类型" / "Adele" / "Hello.mp3").exists()
