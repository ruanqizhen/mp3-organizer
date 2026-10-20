"""把杂乱的长文件名整理成标准格式（默认"演唱者 - 标题.mp3"）。

两档：正则先试——已经是标准格式的文件跳过，不花钱；
--llm 启用时，杂乱文件名交大模型解析出真正的演唱者/曲名。
解析出的演唱者/标题会顺手填入空的标签字段（不覆盖已有值）。
"""
from __future__ import annotations

import re
from pathlib import Path

from .filename_tags import guess_from_filename
from .organize import sanitize

DEFAULT_FORMAT = "{artist} - {title}"


def standard_name(artist: str, title: str, album: str = "",
                  fmt: str = DEFAULT_FORMAT) -> str:
    """按模板拼出标准文件名（不带后缀）；演唱者为空时自动去掉开头的分隔符。"""
    parts = {"artist": sanitize(artist) if artist.strip() else "",
             "title": sanitize(title) if title.strip() else "",
             "album": sanitize(album) if album.strip() else ""}
    name = fmt.format(**parts).strip()
    name = re.sub(r"^[\s\-–—]+", "", name)  # " - 标题" → "标题"
    name = re.sub(r"\s{2,}", " ", name)
    return name or "未知"


def _already_standard(path: Path, fmt: str = DEFAULT_FORMAT) -> bool:
    guess = guess_from_filename(path)
    if not (guess["artist"] and guess["title"]):
        return False
    return path.stem == standard_name(
        guess["artist"], guess["title"], guess.get("album", ""), fmt)


def plan_rename(
    path: str | Path,
    *,
    fmt: str = DEFAULT_FORMAT,
    llm_config=None,
    llm_cache_dir: str | Path | None = None,
    _parse_fn=None,
) -> dict:
    """规划一次改名（不碰文件）。

    返回 dict：path / status（planned|skip）/ artist / title / confidence /
    source（llm|regex）/ new_name / new_path / reason / llm_error。
    _parse_fn(names) -> list[dict] 供测试注入。
    """
    path = Path(path)
    result = {"path": str(path), "status": "skip", "artist": "", "title": "",
              "confidence": 0, "source": "", "new_name": "", "new_path": "",
              "reason": "", "llm_error": ""}
    if _already_standard(path, fmt):
        result["reason"] = "已是标准格式"
        return result

    artist = title = ""
    confidence = 0
    source = ""
    if llm_config is not None or _parse_fn is not None:
        try:
            if _parse_fn is not None:
                items = _parse_fn([path.name])
            else:
                from .llm_tagger import parse_filenames
                items = parse_filenames([path.name], llm_config,
                                        cache_dir=llm_cache_dir)
            item = items[0] if items else {}
            artist = str(item.get("artist", "") or "").strip()
            title = str(item.get("title", "") or "").strip()
            confidence = item.get("confidence", 0)
            source = "llm"
        except Exception as exc:  # noqa: BLE001 - 大模型失败就回退正则
            result["llm_error"] = "%s: %s" % (type(exc).__name__, exc)

    if not artist and not title:
        guess = guess_from_filename(path)
        artist, title = guess["artist"], guess["title"]
        if artist or title:
            source = "regex"

    if not artist and not title:
        result["reason"] = "无法解析出演唱者/标题"
        return result

    new_name = standard_name(artist, title, "", fmt) + path.suffix.lower()
    new_path = path.parent / new_name
    if new_path.resolve() == path.resolve():
        result["reason"] = "已是标准格式"
        return result
    if new_path.exists():
        stem, suffix = new_path.stem, new_path.suffix
        i = 1
        while new_path.with_name(f"{stem} ({i}){suffix}").exists():
            i += 1
        new_path = new_path.with_name(f"{stem} ({i}){suffix}")

    result.update(status="planned", artist=artist, title=title,
                  confidence=confidence, source=source,
                  new_name=new_path.name, new_path=str(new_path))
    return result
