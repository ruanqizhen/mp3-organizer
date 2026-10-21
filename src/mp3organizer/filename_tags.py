"""从文件名猜测标签：处理没有任何标签的 MP3。

常见命名格式（按此顺序尝试）：
    演唱者 - 标题.mp3
    演唱者-标题.mp3
    01. 演唱者 - 标题.mp3 / 001_标题.mp3（带序号）
    演唱者 - 专辑 - 标题.mp3
    [320K]演唱者 - 夜曲.mp3（码率/格式等无意义标记会被剔除）
    标题.mp3（只有标题时）

约定：两段式默认是“演唱者 - 标题”顺序（中文收藏的主流写法）；
三段式按“演唱者 - 专辑 - 标题”理解。猜出的文字同样过一遍编码修复，
以防文件名本身也是跨平台拷回来的乱码。
"""
from __future__ import annotations

import re
from pathlib import Path

# 无意义的后缀标记（码率/格式/音质），猜测时剔除
_JUNK_RE = re.compile(
    r"[\[\(]"
    r"\s*(320\s*k(bps)?|192\s*k(bps)?|128\s*k(bps)?|64\s*k(bps)?"
    r"|ape|flac|wav|mp3|wma|ogg|HQ|SQ)"
    r"\s*[\]\)]",
    re.IGNORECASE,
)
# 开头序号：01. / 001_ / [03] 等
_TRACK_RE = re.compile(r"^\s*(?:[\[\(])?(\d{1,3})(?:[\]\)])?\s*[.\-_ \t]+\s*")
# 开头方括号段（如 [华语]），通常是分类/来源标记，整体去掉
_BRACKET_LEAD_RE = re.compile(r"^\s*[\[\(]\s*(.+?)\s*[\]\)]\s*")


# 开头站点前缀（如 www.snowice.net- / xiami.com_），下载站标记，整体去掉
_SITE_PREFIX_RE = re.compile(
    r"^\s*(?:www\.)?[\w-]+\.(?:com|net|org|cn|com\.cn|io|tv|fm)"
    r"\s*[-_]\s*",
    re.IGNORECASE,
)


def _clean_stem(stem: str) -> str:
    stem = _JUNK_RE.sub("", stem)
    stem = _SITE_PREFIX_RE.sub("", stem)
    return re.sub(r"\s+", " ", stem).strip()


def _split_parts(text: str) -> list[str]:
    # 优先按带空格的分隔符切，避免误伤标题里的连字符
    for sep in (" - ", " – ", " — "):
        if sep in text:
            return [p.strip() for p in text.split(sep)]
    for sep in ("–", "—"):
        if sep in text:
            return [p.strip() for p in text.split(sep)]
    if text.count("-") == 1:
        left, right = text.split("-", 1)
        if left.strip() and right.strip():
            return [left.strip(), right.strip()]
    return [text.strip()]


def guess_from_filename(path: str | Path) -> dict:
    """从文件名猜测标签。

    返回 {"title", "artist", "album", "track"}，猜不出为空字符串。
    """
    from .encoding import fix_text  # 延迟导入，避免循环

    stem = _clean_stem(Path(path).stem)
    result = {"title": "", "artist": "", "album": "", "track": ""}

    m = _TRACK_RE.match(stem)
    if m:
        result["track"] = m.group(1).lstrip("0") or "0"
        stem = stem[m.end():].strip()

    m = _BRACKET_LEAD_RE.match(stem)
    if m:
        inner, rest = m.group(1).strip(), stem[m.end():].strip()
        stem = rest or inner  # 括号后还有内容则丢掉括号段，否则用括号内容当标题

    parts = [p for p in _split_parts(stem) if p]
    if len(parts) >= 3:
        result["artist"], result["album"] = parts[0], parts[1]
        result["title"] = " - ".join(parts[2:])
    elif len(parts) == 2:
        result["artist"], result["title"] = parts
    elif len(parts) == 1:
        result["title"] = parts[0]

    # 标题里残留的音轨号（如 "01.放开"）也拆出来
    if result["title"]:
        m = _TRACK_RE.match(result["title"])
        if m and not result["track"]:
            result["track"] = m.group(1).lstrip("0") or "0"
            result["title"] = result["title"][m.end():].strip()

    for key in ("title", "artist", "album"):
        if result[key]:
            fixed, _ = fix_text(result[key])
            result[key] = fixed
    return result


def _empty_guess() -> dict:
    return {"title": "", "artist": "", "album": "", "track": ""}


def guess_tags(
    path: str | Path,
    *,
    llm_config=None,
    llm_cache_dir: str | Path | None = None,
    _llm_fn=None,
) -> tuple[dict, str, str]:
    """两档猜测：先正则，大模型做回退。

    - 未启用大模型（llm_config 和 _llm_fn 都没给）：沿用旧规则，正则猜出
      标题或演唱者之一即算成功。
    - 启用大模型：正则必须同时猜出演唱者+标题才算成功；否则问大模型。
      大模型失败或也猜不出时，退回正则的部分结果（若有）。
    返回 (guess_dict, source, error)；source 为 "regex" / "llm" / "none"，
    error 非空表示大模型调用失败（已回退，不抛异常）。
    _llm_fn(names) -> list[dict] 供测试注入。
    """
    regex_guess = guess_from_filename(path)
    regex_full = bool(regex_guess["title"] and regex_guess["artist"])
    regex_partial = bool(regex_guess["title"] or regex_guess["artist"])
    llm_wanted = llm_config is not None or _llm_fn is not None

    if regex_full or not llm_wanted:
        if regex_partial:
            return regex_guess, "regex", ""
        return _empty_guess(), "none", ""

    try:
        if _llm_fn is not None:
            items = _llm_fn([Path(path).name])
        else:
            from .llm_tagger import query_llm
            items = query_llm([Path(path).name], llm_config, cache_dir=llm_cache_dir)
        llm_error = ""
    except Exception as exc:  # noqa: BLE001 - 网络/解析失败都回退，不中断流程
        items, llm_error = [], "%s: %s" % (type(exc).__name__, exc)

    item = items[0] if items else {}
    guess = {
        "title": str(item.get("title", "") or "").strip(),
        "artist": str(item.get("artist", "") or "").strip(),
        "album": str(item.get("album", "") or "").strip(),
        "track": str(item.get("track", "") or "").strip(),
        "genre": str(item.get("genre", "") or "").strip(),
        "year": str(item.get("year", "") or "").strip(),
        "confidence": item.get("confidence", 0),
    }
    if guess["title"] or guess["artist"]:
        return guess, "llm", llm_error
    if regex_partial:
        return regex_guess, "regex", llm_error
    return _empty_guess(), "none", llm_error
