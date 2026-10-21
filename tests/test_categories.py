"""categories 模块测试：学习映射、规则、LLM 兜底。"""
from mp3organizer.categories import (
    CANONICAL_CATEGORIES,
    PENDING_CATEGORY,
    normalize_artist,
    learn_from_library,
    guess_by_rules,
    classify,
)


def test_normalize_artist():
    assert normalize_artist("  孙楠,   那英   ") == "孙楠, 那英"
    assert normalize_artist("F.I.R.") == "f.i.r"  # 去尾点（Windows 目录存不住）
    assert normalize_artist("Beyond") == "beyond"
    assert normalize_artist("") == ""
    assert normalize_artist(None) == ""


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")


def _seed_library(lib):
    _touch(lib / "华语.男" / "周杰伦" / "周杰伦 - 夜曲.mp3")
    _touch(lib / "华语.男" / "周杰伦" / "周杰伦 - 晴天.mp3")
    _touch(lib / "华语.双人" / "周杰伦, 温岚 - 屋顶.mp3")
    _touch(lib / "华语.女" / "王菲 - 红豆.mp3")


def test_learn_from_library(tmp_path):
    lib = tmp_path / "lib"
    _seed_library(lib)
    learned = learn_from_library(lib)
    assert learned[normalize_artist("周杰伦")] == "华语.男"
    assert learned[normalize_artist("周杰伦, 温岚")] == "华语.双人"
    assert learned[normalize_artist("王菲")] == "华语.女"


def test_learn_majority_wins(tmp_path):
    lib = tmp_path / "lib"
    _touch(lib / "华语.男" / "周杰伦" / "a.mp3")
    _touch(lib / "华语.男" / "周杰伦" / "b.mp3")
    _touch(lib / "英语" / "周杰伦 - Julia.mp3")  # 散放的一首英文歌
    learned = learn_from_library(lib)
    assert learned[normalize_artist("周杰伦")] == "华语.男"


def test_learn_skips_dashless_and_missing(tmp_path):
    lib = tmp_path / "lib"
    _touch(lib / "英语" / "Hello.mp3")  # 无横线，无法归因
    assert learn_from_library(lib) == {}
    assert learn_from_library(tmp_path / "nope") == {}


def test_rules_pure_music():
    assert guess_by_rules("久石让", "Summer", "New Age") == "纯音乐"
    assert guess_by_rules("x", "y", "古典") == "纯音乐"


def test_rules_duet():
    assert guess_by_rules("孙楠, 那英", "只要有你") == "华语.双人"
    assert guess_by_rules("周深，于文文", "暗香") == "华语.双人"
    # 拉丁双人走语种规则，不进华语.双人
    assert guess_by_rules("A, B", "Song") == "英语"


def test_rules_language():
    assert guess_by_rules("Adele", "Hello") == "英语"
    assert guess_by_rules("", "君をのせて") == "其它语言"
    assert guess_by_rules("谷村新司", "星") is None  # 日文汉字歌，规则不敢定
    assert guess_by_rules("周杰伦", "夜曲") is None  # 华语单人无法分男女
    assert guess_by_rules("", "") is None


def test_classify_learned_first(tmp_path):
    lib = tmp_path / "lib"
    _seed_library(lib)
    learned = learn_from_library(lib)
    cat, source, err = classify({"artist": "周杰伦", "title": "稻香"}, learned)
    assert (cat, source, err) == ("华语.男", "learned", "")


def test_classify_heuristic():
    cat, source, _ = classify({"artist": "Adele", "title": "Hello"}, {})
    assert (cat, source) == ("英语", "heuristic")


def test_classify_llm_and_pending():
    def fake_llm(pairs):
        assert pairs == [("王菲", "红豆")]
        return [{"category": "华语.女", "confidence": 0.99}]

    cat, source, err = classify(
        {"artist": "王菲", "title": "红豆"}, {},
        _classify_fn=fake_llm)
    assert (cat, source, err) == ("华语.女", "llm", "")

    def bad_llm(pairs):
        raise RuntimeError("boom")

    cat, source, err = classify(
        {"artist": "王菲", "title": "红豆"}, {}, _classify_fn=bad_llm)
    assert cat == PENDING_CATEGORY and source == "pending"
    assert "boom" in err

    def weird_llm(pairs):
        return [{"category": "火星语", "confidence": 0.5}]

    cat, source, err = classify(
        {"artist": "王菲", "title": "红豆"}, {}, _classify_fn=weird_llm)
    assert cat == PENDING_CATEGORY and "火星语" in err

    # 无大模型：未知华语单人进待分类
    cat, source, err = classify({"artist": "王菲", "title": "红豆"}, {})
    assert (cat, source, err) == (PENDING_CATEGORY, "pending", "")


def test_canonical_categories_stable():
    assert set(CANONICAL_CATEGORIES) == {
        "华语.女", "华语.男", "华语.组合", "华语.双人",
        "英语", "纯音乐", "其它语言",
    }
