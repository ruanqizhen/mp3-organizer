"""rename 模块测试：文件名标准化规划。"""
from pathlib import Path

from mp3organizer.rename import plan_rename, standard_name


def _fake_parse(names):
    assert names == ["[320K]周杰伦-夜曲(官方正式版)_免费下载.mp3"]
    return [{"artist": "周杰伦", "title": "夜曲", "confidence": 0.95}]


def test_standard_name():
    assert standard_name("周杰伦", "夜曲") == "周杰伦 - 夜曲"
    assert standard_name("", "夜曲") == "夜曲"  # 演唱者为空不留 " - "
    assert standard_name("王菲", "红豆", fmt="{title} - {artist}") == "红豆 - 王菲"


def test_plan_rename_llm(tmp_path):
    p = tmp_path / "[320K]周杰伦-夜曲(官方正式版)_免费下载.mp3"
    p.write_bytes(b"\x00" * 16)
    plan = plan_rename(p, _parse_fn=_fake_parse)
    assert plan["status"] == "planned"
    assert (plan["artist"], plan["title"]) == ("周杰伦", "夜曲")
    assert plan["source"] == "llm" and plan["confidence"] == 0.95
    assert plan["new_name"] == "周杰伦 - 夜曲.mp3"
    assert plan["new_path"] == str(tmp_path / "周杰伦 - 夜曲.mp3")


def test_plan_rename_already_standard_skipped(tmp_path):
    p = tmp_path / "周杰伦 - 夜曲.mp3"
    p.write_bytes(b"\x00" * 16)

    def boom(names):
        raise AssertionError("已是标准格式，不该调大模型")

    plan = plan_rename(p, _parse_fn=boom)
    assert plan["status"] == "skip"
    assert plan["reason"] == "已是标准格式"


def test_plan_rename_regex_fallback(tmp_path):
    p = tmp_path / "王菲-红豆.mp3"
    p.write_bytes(b"\x00" * 16)
    plan = plan_rename(p)  # 不配大模型，走正则
    assert plan["status"] == "planned"
    assert plan["source"] == "regex"
    assert plan["new_name"] == "王菲 - 红豆.mp3"


def test_plan_rename_collision(tmp_path):
    (tmp_path / "周杰伦 - 夜曲.mp3").write_bytes(b"\x00" * 16)  # 目标已存在
    p = tmp_path / "[320K]周杰伦-夜曲(官方正式版)_免费下载.mp3"
    p.write_bytes(b"\x00" * 32)
    plan = plan_rename(p, _parse_fn=_fake_parse)
    assert plan["new_name"] == "周杰伦 - 夜曲 (1).mp3"


def test_plan_rename_unparseable(tmp_path):
    p = tmp_path / "[320K].mp3"
    p.write_bytes(b"\x00" * 16)
    plan = plan_rename(p, _parse_fn=lambda names: [{}])
    assert plan["status"] == "skip"
    assert "无法解析" in plan["reason"]


def test_plan_rename_llm_error_recorded(tmp_path):
    p = tmp_path / "王菲-红豆.mp3"
    p.write_bytes(b"\x00" * 16)

    def bad(names):
        raise RuntimeError("down")

    plan = plan_rename(p, _parse_fn=bad)
    assert "down" in plan["llm_error"]
    # 大模型失败，回退正则
    assert plan["status"] == "planned"
    assert plan["source"] == "regex"
    assert plan["new_name"] == "王菲 - 红豆.mp3"
