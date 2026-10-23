"""textnorm 模块测试：繁转简与降级。"""
import sys

from mp3organizer.textnorm import has_cjk, has_other_script, to_simplified


def test_to_simplified_converts_traditional():
    assert to_simplified("黃明志 鄧紫棋 亞洲通吃") == "黄明志 邓紫棋 亚洲通吃"


def test_to_simplified_skips_non_chinese():
    assert to_simplified("Adele Hello") == "Adele Hello"
    assert to_simplified("") == ""


def test_to_simplified_skips_kana_mixed():
    text = "君をのせて"
    assert has_other_script(text)
    assert to_simplified(text) == text
    assert has_cjk("夜曲") and not has_cjk("Hello")


def test_to_simplified_without_opencc(monkeypatch):
    monkeypatch.setitem(sys.modules, "opencc", None)
    assert to_simplified("黃明志") == "黃明志"
