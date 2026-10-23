"""MusicBrainz 交叉验证：LLM 校对后的 title/artist 去 MB 查 recording，
高分命中时采用 MB 的标准值（权威数据库，零幻觉），并补全 LLM
留空的 album/year。

与 cover.py 的关系：cover 搜 release 找封面，这里搜 recording 校对
标签；共用 musicbrainzngs 的 useragent 与 1 次/秒限流。
search_fn 参数可注入假实现，便于测试。
"""
from __future__ import annotations

import time

import musicbrainzngs

from .cover import APP_NAME, APP_VERSION, APP_CONTACT, match_score
from .textnorm import to_simplified

_mb_ready = False

#: 默认最低分：difflib 0~1，0.85 只接受近乎一致的命中
DEFAULT_MIN_SCORE = 0.85


def _mb_setup() -> None:
    global _mb_ready
    if not _mb_ready:
        musicbrainzngs.set_useragent(APP_NAME, APP_VERSION, APP_CONTACT)
        _mb_ready = True


def lookup_recording(
    artist: str,
    title: str,
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    search_fn=None,
) -> dict | None:
    """查 MB recording，返回高分命中的标准值或 None。

    返回 {title, artist, album, year, mbid, score}：
      title/artist 为 MB 的标准写法（已转简体），
      album/year 取首个发行版本（仅供补空，不覆盖已有值），
      score 为与输入的匹配分。
    无高分命中 / 查询失败返回 None（不抛异常，不阻断流水线）。
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return None
    if search_fn is not None:
        try:
            recordings = search_fn(artist, title)
        except Exception:
            return None
    else:
        _mb_setup()
        time.sleep(1.0)  # 遵守 MusicBrainz 限流
        query = f'artist:"{artist}" AND recording:"{title}"'
        try:
            resp = musicbrainzngs.search_recordings(query=query, limit=5)
        except Exception:
            return None
        recordings = resp.get("recording-list", [])
    best = None
    for rec in recordings:
        cand_artist = rec.get("artist-credit-phrase", "")
        cand_title = rec.get("title", "")
        # MB 中文条目常用繁体，先转简体再比，否则"周杰伦/周杰倫"会被误判为低分
        score = match_score(
            to_simplified(artist), to_simplified(title), "",
            to_simplified(cand_artist), to_simplified(cand_title), "",
        )
        if best is None or score > best["score"]:
            best = {"rec": rec, "score": score}
    if best is None or best["score"] < min_score:
        return None
    rec = best["rec"]
    album, year = "", ""
    releases = rec.get("release-list", [])
    if releases:
        album = releases[0].get("title", "")
        date = releases[0].get("date", "") or rec.get("first-release-date", "")
        if len(date) >= 4 and date[:4].isdigit():
            year = date[:4]
    return {
        "title": to_simplified(rec.get("title", "")),
        "artist": to_simplified(rec.get("artist-credit-phrase", "")),
        "album": to_simplified(album),
        "year": year,
        "mbid": rec.get("id", ""),
        "score": round(best["score"], 3),
    }


def cross_check(
    items: list[dict],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    search_fn=None,
    progress=None,
) -> list[dict | None]:
    """批量交叉验证：items 为 LLM verify 结果（含 title/artist）。

    同一 (artist, title) 只查一次 MB（当次运行内去重）。返回与 items
    对齐的 lookup_recording 结果，无高分命中/失败处为 None。
    progress(done, total, label)：每次实际查询前回调。
    """
    # 去重：同一演唱者+标题只查一次
    uniq: dict[tuple[str, str], int] = {}
    order: list[tuple[str, str]] = []
    for i, it in enumerate(items):
        key = (to_simplified(it.get("artist") or "").strip(),
               to_simplified(it.get("title") or "").strip())
        if key[0] and key[1] and key not in uniq:
            uniq[key] = len(order)
            order.append(key)
    results: list[dict | None] = [None] * len(order)
    for idx, (artist, title) in enumerate(order):
        if progress is not None:
            progress(idx, len(order), f"{artist} - {title}")
        results[idx] = lookup_recording(
            artist, title, min_score=min_score, search_fn=search_fn)

    def _key(it: dict) -> tuple[str, str] | None:
        a = to_simplified(it.get("artist") or "").strip()
        t = to_simplified(it.get("title") or "").strip()
        return (a, t) if a and t else None

    return [results[uniq[k]] if (k := _key(it)) else None for it in items]
