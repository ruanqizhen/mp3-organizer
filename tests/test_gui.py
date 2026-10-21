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


def _make_app(monkeypatch, tmp_path):
    """有显示器时构造 App；HOME 指到 tmp_path，无显示器时跳过。"""
    import tkinter as tk
    import pytest
    from pathlib import Path
    from mp3organizer import gui as gui_mod
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # gui 模块里 from pathlib import Path，绑的是同一个类，打补丁生效
    assert gui_mod.Path is Path
    try:
        return gui_mod.App()
    except tk.TclError:
        pytest.skip("无显示器")


def test_gui_state_save_and_load_roundtrip(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    try:
        app._entries["root"].delete(0, "end")
        app._entries["root"].insert(0, "D:/music")
        app._entries["library"].delete(0, "end")
        app._entries["library"].insert(0, "D:/lib")
        app._vars["dry_run"].set(False)
        app._vars["do_cover"].set(False)
        app._template.set("classic")
        app._subdir_threshold.set("5")
        app._save_gui_state()
    finally:
        app.destroy()

    app2 = _make_app(monkeypatch, tmp_path)
    try:
        assert app2._entries["root"].get() == "D:/music"
        assert app2._entries["library"].get() == "D:/lib"
        assert app2._entries["backup"].get() == ""
        assert app2._vars["dry_run"].get() is False
        assert app2._vars["do_cover"].get() is False
        assert app2._vars["do_tags"].get() is True  # 默认值保留
        assert app2._template.get() == "classic"
        assert app2._subdir_threshold.get() == "5"
        assert app2._min_score.get() == "0.75"
    finally:
        app2.destroy()


def test_gui_state_corrupt_file_ignored(monkeypatch, tmp_path):
    p = tmp_path / ".config" / "mp3-organizer" / "gui.json"
    p.parent.mkdir(parents=True)
    p.write_text("{不是 json", encoding="utf-8")
    app = _make_app(monkeypatch, tmp_path)
    try:
        assert app._entries["root"].get() == ""
        assert app._vars["dry_run"].get() is True
        assert app._template.get() == "personal"
    finally:
        app.destroy()


def test_gui_state_invalid_template_falls_back(monkeypatch, tmp_path):
    import json
    p = tmp_path / ".config" / "mp3-organizer" / "gui.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"template": "bogus", "vars": {"dry_run": "yes"}}),
                 encoding="utf-8")
    app = _make_app(monkeypatch, tmp_path)
    try:
        assert app._template.get() == "personal"  # 非法值不用
        assert app._vars["dry_run"].get() is True  # 非 bool 不用
    finally:
        app.destroy()


def test_llm_settings_missing_verify_keeps_new_default(monkeypatch, tmp_path):
    """老版本存的 llm.json 没有 verify 键时，不把新默认值 True 抹成 False。"""
    import json
    cfg = tmp_path / ".config" / "mp3-organizer" / "llm.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"enabled": True, "base_url": "https://x",
                               "model": "m"}), encoding="utf-8")
    app = _make_app(monkeypatch, tmp_path)
    try:
        assert app._llm_enabled.get() is True
        assert app._llm_verify.get() is True  # 缺键 → 保留新默认
    finally:
        app.destroy()


def test_llm_settings_explicit_verify_false_respected(monkeypatch, tmp_path):
    """用户明确存过 verify=false 时，照旧恢复 False。"""
    import json
    cfg = tmp_path / ".config" / "mp3-organizer" / "llm.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"enabled": True, "verify": False}),
                   encoding="utf-8")
    app = _make_app(monkeypatch, tmp_path)
    try:
        assert app._llm_verify.get() is False
    finally:
        app.destroy()
