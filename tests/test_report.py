"""report 模块测试：改动标注。"""
from mp3organizer.report import _fmt_changes


def test_fmt_changes_marks():
    out = _fmt_changes([
        {"frame": "TIT2", "old": "a", "new": "b", "encoding": "gbk"},
        {"frame": "TPE1", "old": "", "new": "王菲", "encoding": "filename"},
        {"frame": "TPE1", "old": "Jay Chou", "new": "周杰伦",
         "encoding": "llm", "confidence": 0.8},
        {"frame": "TPE1", "old": "GEMblog", "new": "邓紫棋",
         "encoding": "llm_verify", "confidence": 0.9},
        {"frame": "TALB", "old": "亞洲通吃", "new": "亚洲通吃",
         "encoding": "t2s"},
    ])
    assert "TIT2: a → b<br>" in out
    assert "文件名猜测" in out
    assert "大模型猜测·置信度0.8" in out
    assert "大模型校对·置信度0.9" in out
    assert "繁转简" in out


def test_fmt_changes_empty():
    assert _fmt_changes([]) == "—"
