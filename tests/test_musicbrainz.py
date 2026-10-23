"""musicbrainz 交叉验证测试：用注入的假 search_fn 验证阈值/去重/回退。"""
import pytest

from mp3organizer.musicbrainz import (
    DEFAULT_MIN_SCORE, cross_check, lookup_recording,
)


def _rec(title, artist, album="", date="", mbid="mbid-1"):
    releases = [{"title": album, "date": date}] if album or date else []
    return {"id": mbid, "title": title, "artist-credit-phrase": artist,
            "release-list": releases}


def test_lookup_high_score_adopts_canonical():
    """高分命中：采用 MB 标准值，补专辑/年份。"""
    def fake(artist, title):
        return [_rec("夜曲", "周杰伦", "十一月的萧邦", "2005-11-01")]
    r = lookup_recording("周杰伦", "夜曲", search_fn=fake)
    assert r is not None
    assert r["title"] == "夜曲" and r["artist"] == "周杰伦"
    assert r["album"] == "十一月的萧邦" and r["year"] == "2005"
    assert r["mbid"] == "mbid-1" and r["score"] >= DEFAULT_MIN_SCORE


def test_lookup_low_score_returns_none():
    """低分命中不采用（防幻觉）。"""
    def fake(artist, title):
        return [_rec("完全不同的歌", "不知名歌手")]
    assert lookup_recording("周杰伦", "夜曲", search_fn=fake) is None


def test_lookup_traditional_candidate_still_matches():
    """MB 条目是繁体时，转简体后再比，不应误判为低分。"""
    def fake(artist, title):
        return [_rec("夜曲", "周傑倫", "十一月的蕭邦", "2005-11-01")]
    r = lookup_recording("周杰伦", "夜曲", search_fn=fake)
    assert r is not None
    assert r["artist"] == "周杰伦"  # 采用值已转简体
    assert r["score"] >= DEFAULT_MIN_SCORE


def test_lookup_failure_returns_none():
    """MB 查询抛异常不阻断，返回 None。"""
    def fake(artist, title):
        raise RuntimeError("boom")
    assert lookup_recording("周杰伦", "夜曲", search_fn=fake) is None


def test_lookup_empty_input_no_query():
    calls = []

    def fake(artist, title):
        calls.append((artist, title))
        return []
    assert lookup_recording("", "夜曲", search_fn=fake) is None
    assert lookup_recording("周杰伦", "", search_fn=fake) is None
    assert calls == []


def test_cross_check_dedups_and_aligns():
    """同一 (演唱者, 标题) 只查一次，结果与输入对齐。"""
    calls = []

    def fake(artist, title):
        calls.append((artist, title))
        return [_rec(title, artist, "专辑A", "2001-01-01")]
    items = [{"title": "夜曲", "artist": "周杰伦"},
             {"title": "夜曲", "artist": "周杰伦"},  # 重复
             {"title": "红豆", "artist": "王菲"},
             {"title": "", "artist": "王菲"}]  # 空标题不查
    out = cross_check(items, search_fn=fake)
    assert len(calls) == 2  # 去重后只查两次
    assert len(out) == 4
    assert out[0] is not None and out[1] is not None
    assert out[0]["album"] == "专辑A" and out[2]["title"] == "红豆"
    assert out[3] is None


def test_cross_check_progress():
    seen = []

    def fake(artist, title):
        return [_rec(title, artist)]
    items = [{"title": "夜曲", "artist": "周杰伦"}]
    cross_check(items, search_fn=fake,
                progress=lambda d, t, label: seen.append((d, t, label)))
    assert seen == [(0, 1, "周杰伦 - 夜曲")]


def test_apply_mb_overrides_and_fills(tmp_path):
    """_apply_mb：标题/演唱者覆盖，专辑/年份只补空。"""
    from mutagen.id3 import ID3, TIT2, TPE1, TALB
    from mp3organizer.encoding import _apply_mb
    audio = ID3()
    audio.add(TIT2(encoding=3, text=["夜曲(Live)"]))  # 与 MB 标准写法不同→被覆盖
    audio.add(TPE1(encoding=3, text=["周杰伦"]))
    audio.add(TALB(encoding=3, text=["已有专辑"]))  # 不覆盖
    mb = {"title": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦",
          "year": "2005", "mbid": "mbid-1", "score": 0.95}
    changes = _apply_mb(audio, mb)
    frames = {c["frame"]: c for c in changes}
    assert frames["TIT2"]["new"] == "夜曲"  # 去掉尾空格
    assert "TPE1" not in frames  # 相同不写
    assert "TALB" not in frames  # 已有不覆盖
    assert frames["TDRC"]["new"] == "2005"  # 补空
    assert all(c["encoding"] == "musicbrainz" for c in changes)
    assert all(c["mbid"] == "mbid-1" for c in changes)


def test_apply_mb_none_noop():
    from mutagen.id3 import ID3
    from mp3organizer.encoding import _apply_mb
    assert _apply_mb(ID3(), None) == []


# ---------- fix_file 集成 ----------

def _make_tagged_mp3(path, title, artist):
    from mutagen.id3 import ID3, TIT2, TPE1, Encoding
    tag = ID3()
    tag.add(TIT2(encoding=Encoding.UTF8, text=[title]))
    tag.add(TPE1(encoding=Encoding.UTF8, text=[artist]))
    tag.save(str(path))
    with open(path, "ab") as f:
        f.write(b"\xff\xfb\x90\x00" * 100)


def _cfg():
    from mp3organizer.llm_tagger import LLMConfig
    return LLMConfig(base_url="https://fake.local", api_key="k", model="m",
                     batch_size=10)


def test_fix_file_mb_fills_album_year(tmp_path, monkeypatch):
    """LLM 校对后 MB 补专辑/年份，changes 标 musicbrainz。"""
    from mutagen.id3 import ID3
    from mp3organizer.encoding import fix_file
    p = tmp_path / "夜曲.mp3"
    _make_tagged_mp3(p, "夜曲", "周杰伦")
    verified = {"title": "夜曲", "artist": "周杰伦", "album": "", "genre": "",
                "year": "", "track": "", "confidence": 0.9, "note": ""}
    monkeypatch.setattr("mp3organizer.llm_tagger.verify_tags",
                        lambda items, config, **kw: [verified])
    mb = {"title": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦",
          "year": "2005", "mbid": "mbid-1", "score": 0.95}
    r = fix_file(p, llm_verify=True, llm_config=_cfg(),
                 mb_verify=True, _mb_fn=lambda a, t, **kw: mb)
    audio = ID3(str(p))
    assert audio["TALB"].text[0] == "十一月的萧邦"
    assert str(audio["TDRC"].text[0]) == "2005"
    mb_changes = [c for c in r["changes"] if c.get("encoding") == "musicbrainz"]
    assert len(mb_changes) == 2


def test_fix_file_mb_failure_does_not_block(tmp_path, monkeypatch):
    """MB 查询失败不阻断，LLM 校对结果照常应用。"""
    from mutagen.id3 import ID3
    from mp3organizer.encoding import fix_file
    p = tmp_path / "夜曲.mp3"
    _make_tagged_mp3(p, "夜曲", "周杰伦")
    verified = {"title": "夜曲", "artist": "周杰伦", "album": "", "genre": "",
                "year": "", "track": "", "confidence": 0.9, "note": ""}
    monkeypatch.setattr("mp3organizer.llm_tagger.verify_tags",
                        lambda items, config, **kw: [verified])

    def boom(a, t, **kw):
        raise RuntimeError("mb down")
    r = fix_file(p, llm_verify=True, llm_config=_cfg(),
                 mb_verify=True, _mb_fn=boom)
    assert r["status"] == "unchanged"  # MB 失败不抛异常；LLM 值与现有相同故无改动
    assert ID3(str(p))["TIT2"].text[0] == "夜曲"


def test_fix_file_mb_disabled(tmp_path, monkeypatch):
    """--no-mb-verify 时不调 MB。"""
    from mp3organizer.encoding import fix_file
    p = tmp_path / "夜曲.mp3"
    _make_tagged_mp3(p, "夜曲", "周杰伦")
    verified = {"title": "夜曲", "artist": "周杰伦", "album": "", "genre": "",
                "year": "", "track": "", "confidence": 0.9, "note": ""}
    monkeypatch.setattr("mp3organizer.llm_tagger.verify_tags",
                        lambda items, config, **kw: [verified])
    calls = []
    r = fix_file(p, llm_verify=True, llm_config=_cfg(), mb_verify=False,
                 _mb_fn=lambda a, t, **kw: calls.append((a, t)))
    assert calls == []
    assert r["status"] in ("verified", "unchanged")


# ---------- tag→文件名因果链 ----------
def test_messy_filename_becomes_clean_via_tags(tmp_path, monkeypatch):
    """用户指出的因果链：文件名脏是因为 tag 没洗好。
    脏文件名 + 空 tag → LLM 校对（mock 按新 prompt 期望返回）→
    organize 出来的文件名自然干净。"""
    from mutagen.id3 import ID3
    from mp3organizer.encoding import fix_file
    from mp3organizer.organize import organize_file, TEMPLATE_CLASSIC
    from mp3organizer.scanner import read_info

    messy = ("湖南卫视芒果TV官方频道 China HunanTV Official Channel - "
             "【岁乐纯享】时光家族#朋友狠狠地破防了！时光家族献唱集体哭成泪人"
             "#时光音乐会Time Concert EP13丨MangoTV.mp3")
    p = tmp_path / "src" / messy
    p.parent.mkdir(parents=True)
    _make_tagged_mp3(p, "", "")  # 空 tag：文件名是唯一信息源

    # mock LLM 按新 prompt 示例7的期望输出返回
    verified = {"title": "朋友", "artist": "时光家族", "album": "时光音乐会",
                "genre": "流行", "year": "", "track": "",
                "confidence": 0.8, "note": ""}
    monkeypatch.setattr("mp3organizer.llm_tagger.verify_tags",
                        lambda items, config, **kw: [verified])

    from mp3organizer.llm_tagger import LLMConfig
    cfg = LLMConfig(base_url="https://fake.local", api_key="k", model="m")
    r = fix_file(p, llm_verify=True, llm_config=cfg, mb_verify=False)
    assert r["status"] == "verified"
    assert ID3(str(p))["TIT2"].text[0] == "朋友"

    info = read_info(p)
    lib = tmp_path / "lib"
    dest = organize_file(p, lib, info, dry_run=True,
                         template=TEMPLATE_CLASSIC)["dest"]
    from pathlib import Path as _P
    assert _P(dest).name == "朋友.mp3", dest  # 不再是 100+ 字符的脏名
