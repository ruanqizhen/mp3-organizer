"""个人归类模板的分类：定一首歌属于哪个"语言.性别"顶层目录。

分层决策（按顺序，命中即返回）：
  1. 现有库学习：扫描已有音乐库，记住每个演唱者放在哪类（票数多者胜）；
  2. 免费规则：纯音乐流派、中文对唱、假名/韩文/俄文、日文等、纯拉丁；
  3. 大模型：语言和性别信息问大模型（需 --llm，按演唱者缓存）；
  4. 待分类：实在定不了，进"待分类"，人工拖一下，下次学习自动记住。
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .scanner import find_mp3_files

CANONICAL_CATEGORIES: tuple[str, ...] = (
    "华语.女",
    "华语.男",
    "华语.组合",
    "华语.双人",
    "英语",
    "纯音乐",
    "其它语言",
)
PENDING_CATEGORY = "待分类"


def normalize_artist(name: str | None) -> str:
    """归一化演唱者名用于查表：压空格、去尾点、不分大小写。"""
    return " ".join((name or "").split()).rstrip(".").casefold()


def learn_from_library(library: str | Path) -> dict[str, str]:
    """扫描已有音乐库，学出 演唱者 → 顶层分类 的映射。

    证据：L2 子目录名（每首歌投一票）、顶层直放文件的"演唱者 - "前缀；
    无横线的文件无法归因，跳过。同一演唱者出现在多类时票数多者胜
    （票数相同取先遍历到的；遍历按目录名/文件名排序，确定性）。
    library 不存在或不是目录时返回空映射。
    """
    library = Path(library)
    votes: dict[str, Counter] = {}
    if not library.is_dir():
        return {}
    for cat_dir in sorted((p for p in library.iterdir() if p.is_dir()),
                          key=lambda p: p.name):
        if cat_dir.name.startswith("."):
            continue
        for mp3 in find_mp3_files(cat_dir):
            try:
                parts = mp3.relative_to(library).parts
            except ValueError:
                continue
            if len(parts) == 3:
                artist = normalize_artist(parts[1])
            elif " - " in mp3.stem:
                artist = normalize_artist(mp3.stem.split(" - ", 1)[0])
            else:
                continue
            if not artist:
                continue
            votes.setdefault(artist, Counter())[cat_dir.name] += 1
    return {artist: counter.most_common(1)[0][0]
            for artist, counter in votes.items()}


def _has_cjk(text: str) -> bool:
    """是否包含汉字（CJK 统一表意文字及扩展，不含假名/谚文）。"""
    for ch in text:
        o = ord(ch)
        if (
            0x4E00 <= o <= 0x9FFF
            or 0x3400 <= o <= 0x4DBF
            or 0xF900 <= o <= 0xFAFF
        ):
            return True
    return False


def _has_other_script(text: str) -> bool:
    """是否包含假名/谚文/西里尔/泰文/阿拉伯文/天城文等非拉丁非汉字字母。"""
    for ch in text:
        o = ord(ch)
        if (
            0x3040 <= o <= 0x30FF  # 平假名/片假名
            or 0xAC00 <= o <= 0xD7AF  # 谚文音节
            or 0x1100 <= o <= 0x11FF  # 谚文素
            or 0x0400 <= o <= 0x04FF  # 西里尔
            or 0x0E00 <= o <= 0x0E7F  # 泰文
            or 0x0600 <= o <= 0x06FF  # 阿拉伯文
            or 0x0900 <= o <= 0x097F  # 天城文
        ):
            return True
    return False


def _is_latin_text(text: str) -> bool:
    """至少含一个字母，且所有字母都在拉丁区（U+2500 以下）。"""
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(ord(c) < 0x2500 for c in letters)


PURE_MUSIC_KEYWORDS: tuple[str, ...] = (
    "new age", "newage", "classical", "instrument", "light music",
    "纯音乐", "轻音乐", "古典",
)


def guess_by_rules(artist: str, title: str = "", genre: str = "") -> str | None:
    """免费规则猜分类；拿不准返回 None（交给大模型或待分类）。

    华语单人无法从标签区分男女/组合，一律返回 None。
    """
    g = (genre or "").casefold()
    if g and any(k in g for k in PURE_MUSIC_KEYWORDS):
        return "纯音乐"
    a = artist or ""
    if ("," in a or "，" in a) and _has_cjk(a):
        return "华语.双人"
    combined = f"{a} {title or ''}"
    if _has_other_script(combined):
        return "其它语言"
    if _is_latin_text(combined):
        return "英语"
    return None


def classify(
    info: dict,
    learned: dict[str, str] | None = None,
    *,
    llm_config=None,
    llm_cache_dir: str | Path | None = None,
    _classify_fn=None,
) -> tuple[str, str, str]:
    """定一首歌的顶层分类。

    返回 (category, source, error)，source 取值：
    learned（现有库）/ heuristic（规则）/ llm（大模型）/ pending（待分类）。
    error 非空表示大模型调用失败或返回异常（已进待分类，不抛异常）。
    _classify_fn([(artist, title)]) -> list[dict] 供测试注入。
    """
    artist = (info.get("artist") or "").strip()
    title = (info.get("title") or "").strip()
    key = normalize_artist(artist)
    if key and learned and key in learned:
        return learned[key], "learned", ""
    rules = guess_by_rules(artist, title, info.get("genre") or "")
    if rules is not None:
        return rules, "heuristic", ""
    if llm_config is not None or _classify_fn is not None:
        try:
            if _classify_fn is not None:
                items = _classify_fn([(artist, title)])
            else:
                from .llm_tagger import classify_artists
                items = classify_artists([(artist, title)], llm_config,
                                         cache_dir=llm_cache_dir)
            item = items[0] if items else {}
        except Exception as exc:  # noqa: BLE001 - 网络/解析失败都进待分类
            return PENDING_CATEGORY, "pending", "%s: %s" % (type(exc).__name__, exc)
        cat = str(item.get("category", "") or "").strip()
        if cat in CANONICAL_CATEGORIES:
            return cat, "llm", ""
        return PENDING_CATEGORY, "pending", "大模型返回未知分类：%r" % cat
    return PENDING_CATEGORY, "pending", ""
