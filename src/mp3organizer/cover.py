"""专辑封面：MusicBrainz 搜索 + Cover Art Archive 下载 + 嵌入 APIC。

流程：
  修复后的 演唱者/标题/专辑 → MusicBrainz 搜 release → 按相似度打分
  → 取最高分 → Cover Art Archive 下 front 封面 → 嵌入 ID3 APIC 帧。
MusicBrainz 覆盖不全时回退到 iTunes Search API（免费免 key）。

准确率控制：match_score 低于 min_score 的进 needs_review，不自动嵌入；
下载的图片本地缓存，避免重复请求；遵守 MusicBrainz 1 次/秒限流。
search_fn / fetch_fn / get_fn 参数可注入假实现，便于测试。
"""
from __future__ import annotations

import difflib
import time
from pathlib import Path

import musicbrainzngs
import requests
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, Encoding

APP_NAME = "mp3-organizer"
APP_VERSION = "0.1.0"
APP_CONTACT = "https://github.com/ruanqizhen/mp3-organizer"

CAA_SIZE = 500  # 下载 500px 缩略图，体积/清晰度平衡
_mb_ready = False


def _mb_setup() -> None:
    global _mb_ready
    if not _mb_ready:
        musicbrainzngs.set_useragent(APP_NAME, APP_VERSION, APP_CONTACT)
        _mb_ready = True


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().split())


def match_score(
    artist: str, title: str, album: str,
    cand_artist: str, cand_title: str, cand_album: str,
) -> float:
    """0~1 的匹配分：演唱者 40% + 标题 40% + 专辑 20%（有专辑时）。"""
    total, weight = 0.0, 0.0
    for have, cand, w in (
        (artist, cand_artist, 0.4),
        (title, cand_title, 0.4),
        (album, cand_album, 0.2),
    ):
        if have and cand:
            total += difflib.SequenceMatcher(None, _norm(have), _norm(cand)).ratio() * w
            weight += w
    return total / weight if weight else 0.0


def search_musicbrainz(
    artist: str, title: str, album: str = "", limit: int = 5, search_fn=None
) -> list[dict]:
    """搜 MusicBrainz release，返回按匹配分排序的候选 [{mbid,title,artist,album,score}]。"""
    if search_fn is not None:
        candidates = search_fn(artist, title, album, limit)
    else:
        _mb_setup()
        time.sleep(1.0)  # 遵守 MusicBrainz 限流
        query = f'artist:"{artist}" AND recording:"{title}"'
        if album:
            query += f' AND release:"{album}"'
        try:
            resp = musicbrainzngs.search_releases(query=query, limit=limit)
        except Exception:
            return []
        candidates = []
        for rel in resp.get("release-list", []):
            credit = (rel.get("artist-credit-phrase") or "")
            candidates.append(
                {
                    "mbid": rel.get("id", ""),
                    "title": rel.get("title", ""),
                    "artist": credit,
                    "album": rel.get("title", ""),
                }
            )
    scored = []
    for cand in candidates:
        if not cand.get("mbid"):
            continue
        cand["score"] = match_score(
            artist, title, album,
            cand.get("artist", ""), cand.get("title", ""), cand.get("album", ""),
        )
        scored.append(cand)
    return sorted(scored, key=lambda c: c["score"], reverse=True)


def fetch_caa_front(
    mbid: str,
    size: int = CAA_SIZE,
    cache_dir: str | Path | None = None,
    timeout: int = 30,
    fetch_fn=None,
) -> bytes | None:
    """从 Cover Art Archive 下载 front 封面；404/无封面返回 None。"""
    cache_path = None
    if cache_dir is not None:
        cache_path = Path(cache_dir) / f"{mbid}-{size}.jpg"
        if cache_path.exists():
            return cache_path.read_bytes()
    if fetch_fn is not None:
        data = fetch_fn(mbid, size)
    else:
        url = f"https://coverartarchive.org/release/{mbid}/front-{size}"
        try:
            resp = requests.get(url, timeout=timeout)
        except requests.RequestException:
            return None
        if resp.status_code == 404:
            return None
        if not resp.ok or not resp.headers.get("Content-Type", "").startswith("image"):
            return None
        data = resp.content
    if data and cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
    return data or None


def search_itunes(
    artist: str, title: str, limit: int = 5, get_fn=None
) -> list[dict]:
    """iTunes Search API 回退：免费免 key，老中文歌覆盖率常更高。"""
    term = f"{artist} {title}".strip()
    if get_fn is not None:
        results = get_fn(term, limit)
    else:
        try:
            resp = requests.get(
                "https://itunes.apple.com/search",
                params={"term": term, "media": "music", "entity": "album", "limit": limit},
                timeout=30,
            )
            results = resp.json().get("results", []) if resp.ok else []
        except requests.RequestException:
            return []
    out = []
    for r in results:
        art = r.get("artworkUrl100", "")
        out.append(
            {
                "album": r.get("collectionName", ""),
                "artist": r.get("artistName", ""),
                "artwork": art.replace("100x100bb", "600x600bb") if art else "",
            }
        )
    return out


def embed_cover(
    path: str | Path,
    image_bytes: bytes,
    mime: str = "image/jpeg",
    *,
    backup_dir: str | Path | None = None,
    backup_root: str | Path | None = None,
    dry_run: bool = False,
) -> dict:
    """把封面嵌入 ID3 APIC 帧（替换已有封面）。"""
    from .encoding import _backup_copy  # 延迟导入，避免循环

    path = Path(path)
    result: dict = {"path": str(path), "status": "embedded", "size": len(image_bytes),
                    "backup": None, "error": None}
    if dry_run:
        return result
    try:
        try:
            audio = ID3(str(path))
        except ID3NoHeaderError:
            audio = ID3()
        for key in [k for k in audio.keys() if k.startswith("APIC")]:
            del audio[key]
        audio.add(
            APIC(encoding=Encoding.UTF8, mime=mime, type=3, desc="Cover", data=image_bytes)
        )
        if backup_dir is not None:
            result["backup"] = str(_backup_copy(path, Path(backup_dir), backup_root))
        audio.save(v2_version=4)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def process_file(
    path: str | Path,
    info: dict,
    *,
    min_score: float = 0.75,
    size: int = CAA_SIZE,
    cache_dir: str | Path | None = None,
    backup_dir: str | Path | None = None,
    backup_root: str | Path | None = None,
    dry_run: bool = False,
    search_fn=None,
    fetch_fn=None,
    itunes_fn=None,
    download_fn=None,
) -> dict:
    """为单个文件搜索并嵌入封面。

    状态：embedded（已嵌入）| needs_review（最佳匹配分太低，需人工确认）
         | no_cover（搜不到/下不到）| skipped（缺演唱者或标题）
    """
    path = Path(path)
    artist, title, album = info.get("artist", ""), info.get("title", ""), info.get("album", "")
    result: dict = {"path": str(path), "status": "skipped", "reason": "",
                    "mbid": None, "score": 0.0}
    if not artist or not title:
        result["reason"] = "缺少演唱者或标题，无法搜索"
        return result

    candidates = search_musicbrainz(artist, title, album, search_fn=search_fn)
    best = candidates[0] if candidates else None
    if best and best["score"] >= min_score:
        image = fetch_caa_front(best["mbid"], size=size, cache_dir=cache_dir, fetch_fn=fetch_fn)
        if image:
            emb = embed_cover(path, image, backup_dir=backup_dir, backup_root=backup_root,
                              dry_run=dry_run)
            result.update(status=emb["status"], mbid=best["mbid"], score=round(best["score"], 3))
            return result
        result.update(status="no_cover", reason="Cover Art Archive 无封面",
                      mbid=best["mbid"], score=round(best["score"], 3))
        return result
    if best:  # 分太低，留给人工
        result.update(status="needs_review", mbid=best["mbid"],
                      score=round(best["score"], 3),
                      reason=f"最佳匹配分 {best['score']:.2f} < {min_score}")
        result["candidates"] = [
            {"mbid": c["mbid"], "title": c["title"], "artist": c["artist"],
             "score": round(c["score"], 3)} for c in candidates[:5]
        ]
        return result
    # MusicBrainz 无结果 → iTunes 回退
    itunes = search_itunes(artist, title, get_fn=itunes_fn)
    if itunes and itunes[0].get("artwork"):
        art_url = itunes[0]["artwork"]
        if download_fn is not None:
            data = download_fn(art_url)
        else:
            try:
                data = requests.get(art_url, timeout=30).content
            except requests.RequestException:
                data = None
        if data:
            emb = embed_cover(path, data, backup_dir=backup_dir, backup_root=backup_root,
                              dry_run=dry_run)
            result.update(status=emb["status"], reason="iTunes 回退")
            return result
    result.update(status="no_cover", reason="MusicBrainz / iTunes 均无结果")
    return result
