"""gui 测试：只测不依赖显示器的纯函数（进度文本格式化）。"""
import os

from mp3organizer.gui import format_current_file


def test_format_current_file_relative():
    got = format_current_file("tags", os.path.join("C:/m", "子", "王菲 - 红豆.mp3"), "C:/m")
    assert got == os.path.join("子", "王菲 - 红豆.mp3")


def test_format_current_file_relpath_error_falls_back(monkeypatch):
    def boom(path, start):
        raise ValueError("different drives")

    monkeypatch.setattr(os.path, "relpath", boom)
    assert format_current_file("tags", "D:/m/a.mp3", "C:/x") == "D:/m/a.mp3"


def test_format_current_file_truncates_long():
    long = "a" * 200 + ".mp3"
    got = format_current_file("organize", long, None)
    assert len(got) == 90 and got.startswith("…") and got.endswith(".mp3")


def test_format_current_file_message_passthrough():
    assert format_current_file("done", "处理完成：10 个文件", "C:/m") == "处理完成：10 个文件"
    assert format_current_file("scan", "", None) == ""


def test_finish_texts():
    from mp3organizer.gui import finish_texts
    assert finish_texts({"total": 10, "records": [1] * 10, "stopped": False}) == \
        ("完成，共 10 个文件", "处理完成")
    assert finish_texts({"total": 10, "records": [1] * 4, "stopped": True}) == \
        ("已停止（已处理 4/10）", "已停止")
    assert finish_texts({"total": 5, "records": []}) == ("完成，共 5 个文件", "处理完成")


def test_app_does_not_shadow_tk_root():
    """App 不得定义 self._root（会覆盖 tkinter 内部方法导致启动崩溃）。"""
    import tkinter as tk
    from pathlib import Path
    import mp3organizer.gui as gui_mod
    assert "_root" in dir(tk.Tk)  # 机制前提
    src = Path(gui_mod.__file__).read_text(encoding="utf-8")
    assert "self._root" not in src


def test_app_builds_widgets():
    """App 能完成初始化；无显示器时跳过。"""
    import tkinter as tk
    import pytest
    from mp3organizer.gui import App
    try:
        app = App()
    except tk.TclError:
        pytest.skip("无显示器")
    try:
        assert app._stop_btn is not None
        assert app._current is not None
    finally:
        app.destroy()
