"""llm_tagger 模块测试：用注入的假 HTTP 响应验证解析/缓存/回退。"""
import json
import os

import pytest

from mp3organizer.llm_tagger import (
    LLMConfig, LLMError, query_llm, _extract_json_array,
)

CFG = LLMConfig(base_url="https://fake.local", api_key="k", model="m", batch_size=2)


def _fake_post(response_obj=None, response_text=None, raise_exc=None):
    calls = []

    def fake_post(url, headers, payload, timeout):
        calls.append({"url": url, "headers": headers, "payload": payload})
        if raise_exc is not None:
            raise raise_exc
        if response_text is not None:
            class R:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    raise ValueError("no json")

            r = R()
            # 让上层走 content 解析分支：直接返回带 content 的 dict
            return {"choices": [{"message": {"content": response_text}}]}
        return response_obj

    return fake_post, calls


def _item(title, artist, conf=0.9):
    return {"title": title, "artist": artist, "album": "专辑",
            "genre": "流行", "year": "2005", "confidence": conf}


def test_query_llm_parses_and_caches(tmp_path):
    body = [_item("夜曲", "周杰伦"), _item("红豆", "王菲")]
    fake_post, calls = _fake_post(
        response_obj={"choices": [{"message": {"content": json.dumps(body)}}]})
    names = ["a.mp3", "b.mp3"]
    res = query_llm(names, CFG, cache_dir=tmp_path, request_fn=fake_post)
    assert [r["title"] for r in res] == ["夜曲", "红豆"]
    assert res[0]["confidence"] == 0.9
    # 第二次全部命中缓存，不再发请求
    res2 = query_llm(names, CFG, cache_dir=tmp_path, request_fn=fake_post)
    assert res2 == res
    assert len(calls) == 1
    # 请求打到正确的端点，带了 Key
    assert calls[0]["url"] == "https://fake.local/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer k"


def test_query_llm_batching(tmp_path):
    body = [_item("t%d" % i, "a%d" % i) for i in range(2)]
    fake_post, calls = _fake_post(
        response_obj={"choices": [{"message": {"content": json.dumps(body)}}]})
    names = ["f%d.mp3" % i for i in range(5)]  # batch_size=2 → 3 批
    query_llm(names, CFG, cache_dir=tmp_path, request_fn=fake_post)
    assert len(calls) == 3
    assert len(calls[0]["payload"]["messages"][1]["content"]) > 0


def test_query_llm_strips_code_fence(tmp_path):
    body = [_item("夜曲", "周杰伦")]
    text = "```json\n%s\n```" % json.dumps(body)
    fake_post, _ = _fake_post(response_text=text)
    res = query_llm(["a.mp3"], CFG, cache_dir=tmp_path, request_fn=fake_post)
    assert res[0]["artist"] == "周杰伦"


def test_query_llm_length_mismatch_returns_empty(tmp_path):
    body = [_item("夜曲", "周杰伦")]  # 只有 1 条，但问了 2 个
    fake_post, _ = _fake_post(
        response_obj={"choices": [{"message": {"content": json.dumps(body)}}]})
    res = query_llm(["a.mp3", "b.mp3"], CFG, cache_dir=tmp_path, request_fn=fake_post)
    assert res == [{}, {}]


def test_query_llm_http_error_raises(tmp_path):
    import requests
    fake_post, _ = _fake_post(raise_exc=requests.ConnectionError("down"))
    with pytest.raises(LLMError):
        query_llm(["a.mp3"], CFG, cache_dir=tmp_path, request_fn=fake_post)


def test_query_llm_unconfigured_raises(tmp_path):
    with pytest.raises(LLMError):
        query_llm(["a.mp3"], LLMConfig(base_url="", model=""),
                  cache_dir=tmp_path)


def test_extract_json_array_garbage():
    assert _extract_json_array("not json at all") == []
    assert _extract_json_array('{"a": 1}') == []


def test_llm_config_from_env(monkeypatch):
    monkeypatch.setenv("MP3ORGANIZER_LLM_BASE_URL", "https://x.local/v1")
    monkeypatch.setenv("MP3ORGANIZER_LLM_API_KEY", "secret")
    monkeypatch.setenv("MP3ORGANIZER_LLM_MODEL", "m1")
    cfg = LLMConfig.from_env()
    assert (cfg.base_url, cfg.api_key, cfg.model) == (
        "https://x.local/v1", "secret", "m1")
    assert cfg.is_configured() and not cfg.needs_key()


def test_llm_config_localhost_needs_no_key():
    cfg = LLMConfig(base_url="http://localhost:11434/v1", api_key="", model="qwen")
    assert cfg.is_configured() and not cfg.needs_key()
    cfg2 = LLMConfig(base_url="https://api.deepseek.com", api_key="", model="deepseek-chat")
    assert cfg2.needs_key()


def test_auth_header_omitted_without_key(tmp_path):
    cfg = LLMConfig(base_url="http://localhost:11434/v1", api_key="", model="qwen")
    body = [_item("夜曲", "周杰伦")]
    fake_post, calls = _fake_post(
        response_obj={"choices": [{"message": {"content": json.dumps(body)}}]})
    query_llm(["a.mp3"], cfg, cache_dir=tmp_path, request_fn=fake_post)
    assert "Authorization" not in calls[0]["headers"]


def test_parse_filenames(tmp_path):
    import json
    from mp3organizer.llm_tagger import parse_filenames
    body = [{"artist": "周杰伦", "title": "夜曲", "confidence": 0.95}]
    fake_post, calls = _fake_post(
        response_obj={"choices": [{"message": {"content": json.dumps(body)}}]})
    res = parse_filenames(["[320K]xxx.mp3"], CFG, cache_dir=tmp_path,
                          request_fn=fake_post)
    assert res[0]["artist"] == "周杰伦"
    assert res[0]["title"] == "夜曲"
    # 与标签猜测用不同的缓存文件
    assert (tmp_path / "llm_rename.json").exists()
    assert not (tmp_path / "llm_tags.json").exists()
    # prompt 里要求忽略噪音
    assert "噪音" in calls[0]["payload"]["messages"][0]["content"]
