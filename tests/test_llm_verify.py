"""大模型优先校对标签测试：verify_tags 批量/缓存、fix_file 接入、pipeline 预批量与逐文件。"""
import json
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TIT2, TPE1, Encoding

from mp3organizer.llm_tagger import (
    LLMConfig, verify_tags, VERIFY_SYSTEM_PROMPT, _query_cached,
)
from mp3organizer.encoding import fix_file

CFG = LLMConfig(base_url="https://fake.local", api_key="k", model="m", batch_size=2)

FRAME = (b"\xff\xfb\x90\x00" + b"\x00" * 100)


def _fake_post(response_text):
    """构造 request_fn 注入：返回固定 JSON 文本，记录调用次数。"""
    calls = []

    def fake_post(url, headers, payload, timeout):
        calls.append(payload)
        return {"choices": [{"message": {"content": response_text}}]}

    return fake_post, calls


def _item(filename, title="", artist=""):
    return {"filename": filename, "title": title, "artist": artist,
            "album": "", "genre": "", "year": "", "track": ""}


def _fake_verify_factory(results_by_filename=None, calls=None):
    def fake_verify(items, config, *, cache_dir=None, request_fn=None,
                    progress=None, stats=None):
        if calls is not None:
            calls.append([it["filename"] for it in items])
        out = []
        for it in items:
            if results_by_filename and it["filename"] in results_by_filename:
                out.append(results_by_filename[it["filename"]])
            else:  # 默认原样返回
                out.append({"title": it["title"], "artist": it["artist"],
                            "album": "", "genre": "", "year": "", "track": "",
                            "confidence": 0.9, "note": ""})
        return out
    return fake_verify


def _make_tagged_mp3(path: Path, title: str, artist: str) -> None:
    tag = ID3()
    tag.add(TIT2(encoding=Encoding.UTF8, text=[title]))
    tag.add(TPE1(encoding=Encoding.UTF8, text=[artist]))
    tag.save(str(path))
    with open(path, "ab") as f:
        f.write(FRAME * 5)


# ---------- verify_tags ----------

def test_verify_tags_sends_filename_and_tags(tmp_path):
    items = [_item("a.mp3", "夜曲", "Jay Chou"),
             _item("b.mp3", "红豆", "王菲")]
    want = [{"title": "夜曲", "artist": "周杰伦", "confidence": 0.99},
            {"title": "红豆", "artist": "王菲", "confidence": 0.99}]
    fake_post, calls = _fake_post(json.dumps(want, ensure_ascii=False))
    got = verify_tags(items, CFG, cache_dir=tmp_path, request_fn=fake_post)
    assert got == want
    # 批量：batch_size=2，2 个 item 一次发完
    assert len(calls) == 1
    sent = json.loads(calls[0]["messages"][1]["content"])
    sent0 = json.loads(sent[0])  # 每个 key 本身是 JSON 字符串
    assert sent0["filename"] == "a.mp3"
    assert "Jay Chou" in sent0["artist"]
    # 缓存：第二次不再请求
    got2 = verify_tags(items, CFG, cache_dir=tmp_path, request_fn=fake_post)
    assert got2 == want
    assert len(calls) == 1


def test_verify_prompt_covers_key_points():
    assert "filename" in VERIFY_SYSTEM_PROMPT
    assert "Jay Chou" in VERIFY_SYSTEM_PROMPT
    assert "周杰伦" in VERIFY_SYSTEM_PROMPT
    assert "path" in VERIFY_SYSTEM_PROMPT
    assert "简体" in VERIFY_SYSTEM_PROMPT
    assert "邓紫棋" in VERIFY_SYSTEM_PROMPT


# ---------- fix_file 接入 ----------

def test_fix_file_llm_verify_cleans_messy_title(tmp_path, monkeypatch):
    messy = "Namewee 黃明志【胡志明的雨】Ft. 越南小天王胡光孝 亞洲通吃 2017 All Eat Asia"
    p = tmp_path / (messy + ".mp3")
    _make_tagged_mp3(p, messy, "Namewee")
    verified = {"title": "胡志明的雨", "artist": "黃明志, 胡光孝",
                "album": "亞洲通吃", "genre": "流行", "year": "2017",
                "track": "", "confidence": 0.9, "note": "拆分标题"}
    calls = []
    monkeypatch.setattr("mp3organizer.llm_tagger.verify_tags",
                        _fake_verify_factory({p.name: verified}, calls))
    r = fix_file(p, llm_verify=True, llm_config=CFG)
    assert r["status"] == "verified"
    audio = ID3(str(p))
    assert audio["TIT2"].text[0] == "胡志明的雨"
    assert audio["TPE1"].text[0] == "黄明志, 胡光孝"  # 大模型漏简体时 t2s 兜底
    assert audio["TALB"].text[0] == "亚洲通吃"
    assert any(c["encoding"] == "t2s" for c in r["changes"])
    assert str(audio["TDRC"].text[0]) == "2017"  # TDRC 读出是 ID3TimeStamp
    assert any(c.get("verified") for c in r["changes"])
    assert len(calls) == 1  # 单文件只查一次


def test_fix_file_llm_verify_unifies_artist_name(tmp_path, monkeypatch):
    p = tmp_path / "song.mp3"
    _make_tagged_mp3(p, "夜曲", "Jay Chou")
    verified = {"title": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦",
                "genre": "流行", "year": "2005", "track": "",
                "confidence": 0.99, "note": "统一艺人名"}
    monkeypatch.setattr("mp3organizer.llm_tagger.verify_tags",
                        _fake_verify_factory({p.name: verified}, None))
    r = fix_file(p, llm_verify=True, llm_config=CFG)
    assert ID3(str(p))["TPE1"].text[0] == "周杰伦"
    assert r["status"] == "verified"


def test_fix_file_llm_verify_keeps_existing_when_llm_empty(tmp_path, monkeypatch):
    p = tmp_path / "song.mp3"
    _make_tagged_mp3(p, "红豆", "王菲")
    # 大模型没给出 artist：保留已有的，不洗掉
    verified = {"title": "红豆", "artist": "", "album": "",
                "genre": "", "year": "", "track": "",
                "confidence": 0.9, "note": ""}
    monkeypatch.setattr("mp3organizer.llm_tagger.verify_tags",
                        _fake_verify_factory({p.name: verified}, None))
    fix_file(p, llm_verify=True, llm_config=CFG)
    audio = ID3(str(p))
    assert audio["TPE1"].text[0] == "王菲"
    assert audio["TIT2"].text[0] == "红豆"


def test_fix_file_llm_verify_no_tag_builds_from_filename(tmp_path, monkeypatch):
    p = tmp_path / "周杰伦 - 夜曲.mp3"
    with open(p, "wb") as f:
        f.write(FRAME * 5)  # 无任何标签
    verified = {"title": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦",
                "genre": "流行", "year": "2005", "track": "",
                "confidence": 0.95, "note": ""}
    monkeypatch.setattr("mp3organizer.llm_tagger.verify_tags",
                        _fake_verify_factory({p.name: verified}, None))
    r = fix_file(p, llm_verify=True, llm_config=CFG)
    assert r["status"] == "verified"
    audio = ID3(str(p))
    assert audio["TIT2"].text[0] == "夜曲"
    assert audio["TPE1"].text[0] == "周杰伦"


def test_fix_file_llm_verify_failure_falls_back(tmp_path, monkeypatch):
    p = tmp_path / "song.mp3"
    _make_tagged_mp3(p, "", "")  # 空标题/演唱者

    def boom(items, config, *, cache_dir=None, request_fn=None):
        raise RuntimeError("net down")

    monkeypatch.setattr("mp3organizer.llm_tagger.verify_tags", boom)
    r = fix_file(p, llm_verify=True, llm_config=CFG,
                 guess_from_filename=True)
    assert "llm_error" in r or r["status"] in ("unchanged", "no_tag", "fixed")


def test_fix_file_llm_verify_skipped_without_config(tmp_path):
    p = tmp_path / "song.mp3"
    _make_tagged_mp3(p, "夜曲", "周杰伦")
    r = fix_file(p, llm_verify=True, llm_config=None)
    assert r["status"] == "unchanged"
    assert r["changes"] == []


def test_query_cached_progress_per_batch(tmp_path):
    """每批请求前回调 progress(done, total, label)，label 用 label_fn 算。"""
    from mp3organizer.llm_tagger import _query_cached, SYSTEM_PROMPT
    items = [{"title": "t%d" % i, "artist": "a", "album": "",
              "genre": "", "year": "", "confidence": 0.9} for i in range(5)]
    fake_post, _ = _fake_post(json.dumps(items))
    seen = []
    cfg = LLMConfig(base_url="https://fake.local", api_key="k", model="m",
                    batch_size=2)
    _query_cached(["f%d" % i for i in range(5)], cfg, SYSTEM_PROMPT, "x.json",
                  cache_dir=tmp_path, request_fn=fake_post,
                  progress=lambda d, t, label: seen.append((d, t, label)),
                  label_fn=lambda k: "L-" + k)
    assert seen == [(0, 5, "L-f0"), (2, 5, "L-f2"), (4, 5, "L-f4")]


def test_verify_tags_progress_shows_filename(tmp_path):
    """verify 的进度 label 是文件名，而不是内部 JSON key。"""
    items = [_item("夜曲.mp3", "夜曲", "周杰伦"),
             _item("红豆.mp3", "红豆", "王菲"),
             _item("hello.mp3", "Hello", "Adele")]
    want = [{"title": it["title"], "artist": it["artist"], "confidence": 0.9}
            for it in items]
    fake_post, _ = _fake_post(json.dumps(want, ensure_ascii=False))
    seen = []
    cfg = LLMConfig(base_url="https://fake.local", api_key="k", model="m",
                    batch_size=2)
    verify_tags(items, cfg, cache_dir=tmp_path, request_fn=fake_post,
                progress=lambda d, t, label: seen.append((d, t, label)))
    assert seen == [(0, 3, "夜曲.mp3"), (2, 3, "hello.mp3")]
    assert all(not s[2].startswith("{") for s in seen)  # 不是 JSON key


def test_classify_artists_progress_shows_artist(tmp_path):
    """classify 的进度 label 是本批第一位演唱者。"""
    from mp3organizer.llm_tagger import classify_artists
    pairs = [("周杰伦", "夜曲"), ("王菲", "红豆"), ("Adele", "Hello")]
    want = [{"category": "华语.男", "confidence": 0.9}] * 3
    fake_post, _ = _fake_post(json.dumps(want, ensure_ascii=False))
    seen = []
    cfg = LLMConfig(base_url="https://fake.local", api_key="k", model="m",
                    batch_size=2)
    classify_artists(pairs, cfg, cache_dir=tmp_path, request_fn=fake_post,
                     progress=lambda d, t, label: seen.append((d, t, label)))
    assert seen == [(0, 3, "周杰伦"), (2, 3, "Adele")]


def test_query_cached_stats_reports_cache_hits(tmp_path):
    """stats 报出总数/缓存命中/实际调用数。"""
    from mp3organizer.llm_tagger import _query_cached, SYSTEM_PROMPT
    items = [{"title": "t%d" % i, "artist": "a", "album": "",
              "genre": "", "year": "", "confidence": 0.9} for i in range(5)]
    fake_post, calls = _fake_post(json.dumps(items))
    cfg = LLMConfig(base_url="https://fake.local", api_key="k", model="m",
                    batch_size=10)
    names = ["f%d" % i for i in range(5)]
    st1: dict = {}
    _query_cached(names, cfg, SYSTEM_PROMPT, "s.json", cache_dir=tmp_path,
                  request_fn=fake_post, stats=st1)
    assert st1 == {"total": 5, "cached": 0, "queried": 5}
    # 第二次全命中缓存，不再请求
    st2: dict = {}
    _query_cached(names, cfg, SYSTEM_PROMPT, "s.json", cache_dir=tmp_path,
                  request_fn=fake_post, stats=st2)
    assert st2 == {"total": 5, "cached": 5, "queried": 0}
    assert len(calls) == 1


def test_query_cached_no_cache_session_dedup(tmp_path):
    """use_cache=False：不读不写持久缓存，但当次运行内去重；
    clear_session_cache() 后重新调用。"""
    from pathlib import Path
    from mp3organizer.llm_tagger import (
        _query_cached, SYSTEM_PROMPT, clear_session_cache)
    items = [{"title": "t0", "artist": "a", "album": "",
              "genre": "", "year": "", "confidence": 0.9}]
    fake_post, calls = _fake_post(json.dumps(items))
    cfg = LLMConfig(base_url="https://fake.local", api_key="k", model="m",
                    batch_size=10, use_cache=False)
    clear_session_cache()
    _query_cached(["f0"], cfg, SYSTEM_PROMPT, "nc.json", cache_dir=tmp_path,
                  request_fn=fake_post)
    _query_cached(["f0"], cfg, SYSTEM_PROMPT, "nc.json", cache_dir=tmp_path,
                  request_fn=fake_post)
    assert len(calls) == 1  # 当次运行内命中内存缓存
    assert not (Path(tmp_path) / "nc.json").exists()  # 没写缓存文件
    clear_session_cache()  # 新的一次运行：重新调用
    _query_cached(["f0"], cfg, SYSTEM_PROMPT, "nc.json", cache_dir=tmp_path,
                  request_fn=fake_post)
    assert len(calls) == 2


def test_verify_tags_sends_path(tmp_path):
    """校验请求带上相对路径。"""
    from mp3organizer.llm_tagger import verify_tags
    body = [{"title": "t", "artist": "a", "album": "", "genre": "",
             "year": "", "track": "", "confidence": 0.9, "note": ""}]
    fake_post, calls = _fake_post(json.dumps(body))
    items = [{"path": "待整理/子/周杰伦 - 夜曲.mp3",
              "filename": "周杰伦 - 夜曲.mp3", "title": "夜曲",
              "artist": "周杰伦", "album": "", "genre": "", "year": "",
              "track": ""}]
    res = verify_tags(items, CFG, cache_dir=tmp_path, request_fn=fake_post)
    assert res[0]["title"] == "t"
    sent = calls[0]["messages"][-1]["content"]
    assert "待整理/子/周杰伦 - 夜曲.mp3" in sent


def test_fix_file_verify_item_includes_path(tmp_path, monkeypatch):
    """fix_file 独立校验也带上相对路径。"""
    sub = tmp_path / "src" / "华语" / "周杰伦"
    sub.mkdir(parents=True)
    p = sub / "夜曲.mp3"
    _make_tagged_mp3(p, "夜曲", "周杰伦")
    seen = {}

    def fake_verify(items, config, **kw):
        seen["items"] = items
        return [{"title": it["title"], "artist": it["artist"], "album": "",
                 "genre": "", "year": "", "track": "",
                 "confidence": 0.9, "note": ""} for it in items]

    monkeypatch.setattr("mp3organizer.llm_tagger.verify_tags", fake_verify)
    fix_file(p, llm_verify=True, llm_config=CFG,
             backup_root=tmp_path / "src")
    assert seen["items"][0]["path"] == "华语/周杰伦/夜曲.mp3"
