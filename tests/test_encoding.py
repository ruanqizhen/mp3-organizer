"""encoding 模块测试：用合成的乱码标签验证修复逻辑。"""
from pathlib import Path

from mutagen.id3 import ID3, TIT2, TPE1, TALB, Encoding

from mp3organizer.encoding import fix_text, fix_file


def garbled(text: str, enc: str = "gbk") -> str:
    """模拟老 Windows 写法：enc 字节被误标为 latin1。"""
    return text.encode(enc).decode("latin1")


def test_fix_text_gbk():
    fixed, enc = fix_text(garbled("周杰伦"))
    assert (fixed, enc) == ("周杰伦", "gbk")


def test_fix_text_big5():
    fixed, enc = fix_text(garbled("劉德華", "big5"), encodings=("big5", "gbk"))
    assert (fixed, enc) == ("劉德華", "big5")


def test_fix_text_ascii_unchanged():
    assert fix_text("Yesterday") == ("Yesterday", None)


def test_fix_text_european_not_harmed():
    # 高位字节太少（café 只有 1 个），自动模式不动它
    assert fix_text("café") == ("café", None)


def test_fix_text_already_unicode():
    assert fix_text("周杰伦") == ("周杰伦", None)


def test_fix_text_force_encoding():
    fixed, enc = fix_text(garbled("王菲"), force="gbk")
    assert (fixed, enc) == ("王菲", "gbk")


def _make_mp3(path, frames: dict):
    tag = ID3()
    for fid, (text,) in frames.items():
        cls = {"TIT2": TIT2, "TPE1": TPE1, "TALB": TALB}[fid]
        tag.add(cls(encoding=Encoding.LATIN1, text=[text]))
    tag.save(str(path))


def test_fix_file(tmp_path):
    p = tmp_path / "a.mp3"
    _make_mp3(p, {"TIT2": (garbled("夜曲"),), "TPE1": (garbled("周杰伦"),)})
    res = fix_file(p, backup_dir=tmp_path / "bak")
    assert res["status"] == "fixed"
    assert len(res["changes"]) == 2
    assert Path(res["backup"]).exists()
    reread = ID3(str(p))
    assert reread["TIT2"].text == ["夜曲"]
    assert reread["TPE1"].text == ["周杰伦"]
    assert reread["TIT2"].encoding == Encoding.UTF8


def test_fix_file_dry_run_does_not_write(tmp_path):
    p = tmp_path / "b.mp3"
    _make_mp3(p, {"TIT2": (garbled("夜曲"),)})
    before = p.read_bytes()
    res = fix_file(p, dry_run=True)
    assert res["status"] == "fixed"
    assert p.read_bytes() == before  # 文件未被改动


def test_fix_file_clean_unchanged(tmp_path):
    p = tmp_path / "c.mp3"
    tag = ID3()
    tag.add(TIT2(encoding=Encoding.UTF8, text=["夜曲"]))
    tag.save(str(p))
    res = fix_file(p)
    assert res["status"] == "unchanged"
    assert res["changes"] == []


def test_fix_file_no_tag(tmp_path):
    p = tmp_path / "d.mp3"
    p.write_bytes(b"\x00" * 64)
    assert fix_file(p)["status"] == "no_tag"
