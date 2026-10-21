"""organize 模块测试。"""
from pathlib import Path

from mp3organizer.categories import PENDING_CATEGORY
from mp3organizer.organize import (
    normalize_genre,
    sanitize,
    target_path,
    target_path_personal,
    organize_file,
    use_artist_subdir,
    TEMPLATE_CLASSIC,
    UNKNOWN_GENRE,
    UNKNOWN_ARTIST,
)


def test_normalize_genre_number():
    assert normalize_genre("(13)") == "Pop"
    assert normalize_genre("(13)Pop") == "Pop"


def test_normalize_genre_empty():
    assert normalize_genre("") == UNKNOWN_GENRE
    assert normalize_genre(None) == UNKNOWN_GENRE


def test_normalize_genre_text_passthrough():
    assert normalize_genre("摇滚") == "摇滚"


def test_sanitize_illegal_chars():
    assert sanitize('a/b:c*d?e"f<g>h|i') == "a_b_c_d_e_f_g_h_i"


def test_sanitize_trailing_dots():
    assert sanitize("abc...") == "abc"


def test_target_path_with_album():
    info = {"path": "/m/x.mp3", "title": "夜曲", "artist": "周杰伦",
            "album": "十一月的萧邦", "genre": "Pop"}
    got = target_path("/lib", info)
    assert got == Path("/lib/Pop/周杰伦/周杰伦 - 十一月的萧邦/夜曲.mp3")


def test_target_path_without_album():
    info = {"path": "/m/x.mp3", "title": "夜曲", "artist": "周杰伦",
            "album": "", "genre": ""}
    got = target_path("/lib", info)
    assert got == Path(f"/lib/{UNKNOWN_GENRE}/周杰伦/夜曲.mp3")


def test_target_path_missing_artist():
    info = {"path": "/m/x.mp3", "title": "夜曲", "artist": "", "album": "", "genre": ""}
    got = target_path("/lib", info)
    assert UNKNOWN_ARTIST in str(got)


def test_organize_file_moves(tmp_path):
    src = tmp_path / "src" / "a.mp3"
    src.parent.mkdir()
    src.write_bytes(b"fake")
    info = {"path": str(src), "title": "夜曲", "artist": "周杰伦", "album": "", "genre": "Pop"}
    res = organize_file(src, tmp_path / "lib", info)
    assert res["status"] == "moved"
    assert Path(res["dest"]).exists()
    assert not src.exists()


def test_organize_file_collision_never_overwrites(tmp_path):
    lib = tmp_path / "lib"
    info = {"path": "x.mp3", "title": "夜曲", "artist": "周杰伦", "album": "", "genre": "Pop"}
    for i in range(2):
        src = tmp_path / f"s{i}.mp3"
        src.write_bytes(f"fake{i}".encode())
        organize_file(src, lib, {**info, "path": str(src)},
                        template=TEMPLATE_CLASSIC)
    dests = sorted((lib / "Pop" / "周杰伦").glob("*.mp3"))
    # 注意：按 Unicode 码点排序，空格(U+0020) < 点号(U+002E)
    assert [d.name for d in dests] == ["夜曲 (1).mp3", "夜曲.mp3"]


# ---- merge_file / _is_duplicate ----

from mp3organizer.organize import merge_file, unique_path, _is_duplicate

FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413  # 417 字节假 MPEG 帧


def _stream(path: Path, n_frames: int):
    path.write_bytes(FRAME * n_frames)


def _info(title="夜曲", artist="周杰伦", genre="流行"):
    return {"path": "/m/x.mp3", "title": title, "artist": artist,
            "album": "", "genre": genre}


def test_is_duplicate_same_content(tmp_path):
    a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
    _stream(a, 20)
    _stream(b, 20)
    assert _is_duplicate(a, b)


def test_is_duplicate_different_size(tmp_path):
    a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
    _stream(a, 20)
    _stream(b, 30)
    assert not _is_duplicate(a, b)


def test_merge_file_skips_duplicate(tmp_path):
    lib = tmp_path / "lib"
    dest = lib / "流行" / "周杰伦" / "夜曲.mp3"
    dest.parent.mkdir(parents=True)
    _stream(dest, 20)
    src = tmp_path / "src" / "夜曲.mp3"
    src.parent.mkdir(parents=True)
    _stream(src, 20)  # 内容相同 → 重复
    r = merge_file(src, lib, _info(), template=TEMPLATE_CLASSIC)
    assert r["status"] == "skipped_duplicate"
    assert src.exists()  # 没被移走


def test_merge_file_renames_when_different(tmp_path):
    lib = tmp_path / "lib"
    dest = lib / "流行" / "周杰伦" / "夜曲.mp3"
    dest.parent.mkdir(parents=True)
    _stream(dest, 20)
    src = tmp_path / "src" / "夜曲.mp3"
    src.parent.mkdir(parents=True)
    _stream(src, 30)  # 内容不同 → 改名并入
    r = merge_file(src, lib, _info(), template=TEMPLATE_CLASSIC)
    assert r["status"] == "renamed"
    assert r["dest"].endswith("夜曲 (1).mp3")
    assert Path(r["dest"]).exists()
    assert not src.exists()


def test_merge_file_replace(tmp_path):
    lib = tmp_path / "lib"
    dest = lib / "流行" / "周杰伦" / "夜曲.mp3"
    dest.parent.mkdir(parents=True)
    _stream(dest, 20)
    src = tmp_path / "src" / "夜曲.mp3"
    src.parent.mkdir(parents=True)
    _stream(src, 30)
    r = merge_file(src, lib, _info(), on_duplicate="replace",
                   template=TEMPLATE_CLASSIC)
    assert r["status"] == "replaced"
    assert Path(r["dest"]).stat().st_size == len(FRAME) * 30


def test_merge_file_moves_new(tmp_path):
    lib = tmp_path / "lib"
    src = tmp_path / "src" / "红豆.mp3"
    src.parent.mkdir(parents=True)
    _stream(src, 20)
    r = merge_file(src, lib, _info(title="红豆", artist="王菲"),
                   template=TEMPLATE_CLASSIC)
    assert r["status"] == "moved"
    assert Path(r["dest"]).exists()


def test_unique_path():
    assert unique_path(Path("/nonexistent/x.mp3")) == Path("/nonexistent/x.mp3")


# ---- personal 模板 ----

def test_target_path_personal_subdir():
    info = {"path": "/m/x.mp3", "title": "夜曲", "artist": "周杰伦"}
    got = target_path_personal("/lib", info, "华语.男", True)
    assert got == Path("/lib/华语.男/周杰伦/周杰伦 - 夜曲.mp3")


def test_target_path_personal_flat_and_no_artist():
    info = {"path": "/m/x.mp3", "title": "Hello", "artist": "Adele"}
    assert target_path_personal("/lib", info, "英语", False) == \
        Path("/lib/英语/Adele - Hello.mp3")
    info2 = {"path": "/m/Chiara.mp3", "title": "Chiara", "artist": ""}
    assert target_path_personal("/lib", info2, "其它语言", True) == \
        Path("/lib/其它语言/Chiara.mp3")  # 无演唱者：只用标题，不建目录


def test_target_path_personal_pending_fallback():
    info = {"path": "/m/x.mp3", "title": "夜曲", "artist": "周杰伦"}
    got = target_path_personal("/lib", info, "", False)
    assert got == Path(f"/lib/{PENDING_CATEGORY}/周杰伦 - 夜曲.mp3")


def test_use_artist_subdir(tmp_path):
    lib = tmp_path / "lib"
    assert use_artist_subdir(lib, "华语.男", "周杰伦", 3, 3)
    assert not use_artist_subdir(lib, "华语.男", "周杰伦", 2, 3)
    assert not use_artist_subdir(lib, "华语.男", "", 9, 3)
    (lib / "华语.男" / "周杰伦").mkdir(parents=True)
    assert use_artist_subdir(lib, "华语.男", "周杰伦", 1, 3)  # 库里已有目录


def test_organize_file_personal_moves(tmp_path):
    src = tmp_path / "src" / "a.mp3"
    src.parent.mkdir()
    src.write_bytes(b"fake")
    info = {"path": str(src), "title": "夜曲", "artist": "周杰伦"}
    res = organize_file(src, tmp_path / "lib", info,
                        category="华语.男", use_subdir=True)
    assert res["status"] == "moved"
    assert res["category"] == "华语.男"
    dest = tmp_path / "lib" / "华语.男" / "周杰伦" / "周杰伦 - 夜曲.mp3"
    assert dest.exists() and not src.exists()


def test_merge_file_personal_moves_new(tmp_path):
    lib = tmp_path / "lib"
    src = tmp_path / "src" / "红豆.mp3"
    src.parent.mkdir(parents=True)
    _stream(src, 20)
    r = merge_file(src, lib, _info(title="红豆", artist="王菲"),
                   category="华语.女", use_subdir=False)
    assert r["status"] == "moved"
    assert Path(r["dest"]) == lib / "华语.女" / "王菲 - 红豆.mp3"


def test_clean_display_name_strips_noise_brackets():
    from mp3organizer.organize import clean_display_name
    assert clean_display_name("夜曲【动态歌词 lyric video】") == "夜曲"
    assert clean_display_name("红豆[Official MV]") == "红豆"
    assert clean_display_name("童话(高清官方版)") == "童话"


def test_clean_display_name_keeps_real_bracket_title():
    from mp3organizer.organize import clean_display_name
    # 括号里是真歌名/真信息，不在噪音词表里 → 保留
    assert clean_display_name("黃明志【胡志明的雨】") == "黃明志【胡志明的雨】"


def test_clean_display_name_standalone_noise_and_separators():
    from mp3organizer.organize import clean_display_name
    assert clean_display_name("篇章 with English 中英") == "篇章"
    assert clean_display_name("张韶涵 _ 王赫野") == "张韶涵、王赫野"
    assert clean_display_name("A / B") == "A、B"


def test_clean_display_name_user_case():
    """用户真实案例：标题标签里塞满演唱者/噪音。"""
    from mp3organizer.organize import clean_display_name
    messy = ("PLAY MUSIC - 篇章 Pages - 张韶涵 _ 王赫野 Angela Chang _ Wang HeYe"
             "【动态歌词 lyric video】with English 中英")
    got = clean_display_name(messy)
    assert "动态歌词" not in got and "lyric video" not in got.lower()
    assert "with English" not in got and not got.endswith("中英")
    assert "、" in got  # 分隔符统一
    assert "篇章" in got


def test_target_path_personal_cleans_filename():
    from mp3organizer.organize import target_path_personal
    info = {"path": "/m/x.mp3",
            "title": "篇章【动态歌词 lyric video】with English 中英",
            "artist": "张韶涵 _ 王赫野"}
    p = target_path_personal("/lib", info, "华语.双人", False)
    assert p.name == "张韶涵、王赫野 - 篇章.mp3"


def test_guess_by_rules_duet_with_ideographic_comma():
    from mp3organizer.categories import guess_by_rules
    assert guess_by_rules("张韶涵、王赫野", "篇章") == "华语.双人"


def test_clean_display_name_strips_lyric_quote():
    from mp3organizer.organize import clean_display_name
    assert clean_display_name("若月亮沒來『若是晨風還沒來，晚風也可吹入懷』") == "若月亮沒來"
    assert clean_display_name("夜曲「風雨」") == "夜曲「風雨」"  # 无标点，保守保留


def test_clean_display_name_artist_underscore():
    from mp3organizer.organize import clean_display_name
    assert clean_display_name("王宇宙Leto_喬浚丞", is_artist=True) == "王宇宙Leto、喬浚丞"
    # 标题不碰无空格下划线
    assert clean_display_name("王宇宙Leto_喬浚丞") == "王宇宙Leto_喬浚丞"


def test_target_path_personal_user_sky_case():
    """用户真实案例：频道名演唱者 + 备选标题 + 歌词引用（不开大模型的兜底）。"""
    from mp3organizer.organize import target_path_personal
    info = {"path": "/m/x.mp3",
            "title": "若月亮沒來 (若是月亮還沒來) - 王宇宙Leto_喬浚丞"
                     "『若是晨風還沒來，晚風也可吹入懷』【動態歌詞】",
            "artist": "SKY Music Channel"}
    p = target_path_personal("/lib", info, "待分类", False)
    assert "動態歌詞" not in p.name
    assert "若是晨風還沒來" not in p.name  # 歌词引用已删
    assert p.name.startswith("SKY Music Channel - ")


def test_llm_verify_enabled_defaults():
    import argparse
    from mp3organizer.cli import _llm_verify_enabled
    ns = argparse.Namespace(llm=True, llm_verify=False, no_llm_verify=False)
    assert _llm_verify_enabled(ns) is True  # --llm 即默认开校对
    ns = argparse.Namespace(llm=True, llm_verify=False, no_llm_verify=True)
    assert _llm_verify_enabled(ns) is False  # --no-llm-verify 可关
    ns = argparse.Namespace(llm=False, llm_verify=True, no_llm_verify=False)
    assert _llm_verify_enabled(ns) is True  # 单独 --llm-verify 也开
    ns = argparse.Namespace(llm=False, llm_verify=False, no_llm_verify=False)
    assert _llm_verify_enabled(ns) is False
