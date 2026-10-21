"""filename_tags 模块测试：各种常见命名格式。"""
from pathlib import Path

from mp3organizer.filename_tags import guess_from_filename


def g(name: str) -> dict:
    return guess_from_filename(Path(f"/m/{name}"))


def test_artist_dash_title():
    r = g("周杰伦 - 夜曲.mp3")
    assert (r["artist"], r["title"]) == ("周杰伦", "夜曲")


def test_artist_dash_title_no_spaces():
    r = g("王菲-红豆.mp3")
    assert (r["artist"], r["title"]) == ("王菲", "红豆")


def test_title_only():
    r = g("夜曲.mp3")
    assert r["title"] == "夜曲" and r["artist"] == ""


def test_track_number_prefix():
    r = g("03. 青花瓷 - 周杰伦.mp3")
    assert r["track"] == "3"
    # 注意：两段式按“演唱者 - 标题”理解，这里是反例，仅验证解析不崩
    assert r["title"]


def test_track_number_artist_first():
    r = g("001_周杰伦 - 夜曲.mp3")
    assert r["track"] == "1"
    assert (r["artist"], r["title"]) == ("周杰伦", "夜曲")


def test_three_parts_artist_album_title():
    r = g("周杰伦 - 十一月的萧邦 - 夜曲.mp3")
    assert (r["artist"], r["album"], r["title"]) == ("周杰伦", "十一月的萧邦", "夜曲")


def test_junk_bitrate_stripped():
    r = g("[320K]周杰伦 - 夜曲.mp3")
    assert (r["artist"], r["title"]) == ("周杰伦", "夜曲")


def test_junk_suffix_stripped():
    r = g("周杰伦 - 夜曲 (320kbps).mp3")
    assert (r["artist"], r["title"]) == ("周杰伦", "夜曲")


def test_leading_bracket_dropped():
    r = g("[华语]周杰伦 - 夜曲.mp3")
    assert (r["artist"], r["title"]) == ("周杰伦", "夜曲")


def test_garbled_filename_also_fixed():
    garbled = "周杰伦 - 夜曲".encode("gbk").decode("latin1")
    r = g(garbled + ".mp3")
    assert (r["artist"], r["title"]) == ("周杰伦", "夜曲")


def test_empty_after_clean():
    r = g("[320K].mp3")
    assert r["title"] == "" and r["artist"] == ""


def test_guess_tags_regex_wins_no_llm_call():
    from mp3organizer.filename_tags import guess_tags

    def boom(names):
        raise AssertionError("正则已猜出，不该调用大模型")

    guess, source, err = guess_tags("/m/周杰伦 - 夜曲.mp3", _llm_fn=boom)
    assert (source, err) == ("regex", "")
    assert (guess["artist"], guess["title"]) == ("周杰伦", "夜曲")


def test_guess_tags_llm_fallback():
    from mp3organizer.filename_tags import guess_tags

    def fake_llm(names):
        assert names == ["yq_zjl.mp3"]
        return [{"title": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦",
                 "genre": "流行", "year": "2005", "confidence": 0.7}]

    guess, source, err = guess_tags("/m/yq_zjl.mp3", _llm_fn=fake_llm)
    assert (source, err) == ("llm", "")
    assert (guess["artist"], guess["title"], guess["album"]) == (
        "周杰伦", "夜曲", "十一月的萧邦")


def test_guess_tags_llm_error_falls_back():
    from mp3organizer.filename_tags import guess_tags

    def bad_llm(names):
        raise RuntimeError("network down")

    guess, source, err = guess_tags("/m/yq_zjl.mp3", _llm_fn=bad_llm)
    assert source == "regex"  # 大模型失败，退回正则的部分结果
    assert "network down" in err
    assert guess["title"] == "yq_zjl"


def test_guess_tags_no_llm_configured():
    from mp3organizer.filename_tags import guess_tags
    guess, source, err = guess_tags("/m/yq_zjl.mp3")
    assert (source, err) == ("regex", "")  # 未启用大模型时沿用旧规则


def test_guess_from_filename_site_prefix_and_mojibake():
    """站点前缀去掉，文件名乱码修好，标题里音轨号拆出来。"""
    from mp3organizer.filename_tags import guess_from_filename
    got = guess_from_filename("www.snowice.net-°®ÀÖÍÅ - 01.放开.mp3")
    assert got["artist"] == "爱乐团", got
    assert got["title"] == "放开", got
    assert got["track"] == "1", got
