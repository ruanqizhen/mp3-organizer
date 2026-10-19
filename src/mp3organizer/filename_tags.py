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


def _clean_stem(stem: str) -> str:
    stem = _JUNK_RE.sub("", stem)
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

    for key in ("title", "artist", "album"):
        if result[key]:
            fixed, _ = fix_text(result[key])
            result[key] = fixed
    return result
