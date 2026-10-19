"""按 类型 / 演唱者 / 专辑 把 MP3 归类到子文件夹。

默认结构：
    <音乐库>/<类型>/<演唱者>/<演唱者 - 专辑>/<曲名>.mp3
无专辑信息时：
    <音乐库>/<类型>/<演唱者>/<曲名>.mp3
缺失的类型/演唱者归入 “未知类型” / “未知演唱者”，便于事后在报告里人工补。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

UNKNOWN_GENRE = "未知类型"
UNKNOWN_ARTIST = "未知演唱者"

# ID3v1 标准类型表（含 Winamp 扩展到 147）
ID3V1_GENRES = [
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge",
    "Hip-Hop", "Jazz", "Metal", "New Age", "Oldies", "Other", "Pop", "R&B",
    "Rap", "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska",
    "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient",
    "Trip-Hop", "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical",
    "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel", "Noise",
    "AlternRock", "Bass", "Soul", "Punk", "Space", "Meditative",
    "Instrumental Pop", "Instrumental Rock", "Ethnic", "Gothic", "Darkwave",
    "Techno-Industrial", "Electronic", "Pop-Folk", "Eurodance", "Dream",
    "Southern Rock", "Comedy", "Cult", "Gangsta", "Top 40", "Christian Rap",
    "Pop/Funk", "Jungle", "Native American", "Cabaret", "New Wave",
    "Psychadelic", "Rave", "Showtunes", "Trailer", "Lo-Fi", "Tribal",
    "Acid Punk", "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll",
    "Hard Rock",
    # Winamp 扩展
    "Folk", "Folk-Rock", "National Folk", "Swing", "Fast Fusion", "Bebob",
    "Latin", "Revival", "Celtic", "Bluegrass", "Avantgarde", "Gothic Rock",
    "Progressive Rock", "Psychedelic Rock", "Symphonic Rock", "Slow Rock",
    "Big Band", "Chorus", "Easy Listening", "Acoustic", "Humour", "Speech",
    "Chanson", "Opera", "Chamber Music", "Sonata", "Symphony", "Booty Bass",
    "Primus", "Porn Groove", "Satire", "Slow Jam", "Club", "Tango", "Samba",
    "Folklore", "Ballad", "Power Ballad", "Rhythmic Soul", "Freestyle",
    "Duet", "Punk Rock", "Drum Solo", "A capella", "Euro-House", "Dance Hall",
    "Goa", "Drum & Bass", "Club-House", "Hardcore", "Terror", "Indie",
    "BritPop", "Negerpunk", "Polsk Punk", "Beat", "Christian Gangsta Rap",
    "Heavy Metal", "Black Metal", "Crossover", "Contemporary Christian",
    "Christian Rock", "Merengue", "Salsa", "Thrash Metal", "Anime", "Jpop",
    "Synthpop",
]

_INVALID_CHARS = '/\\:*?"<>|'


def normalize_genre(raw: str | None) -> str:
    """把 TCON 原始值转成可读类型名。

    处理 ID3v2.3 的 "(13)" / "(13)Pop" 数字编号；空值返回 未知类型。
    """
    raw = (raw or "").strip()
    if not raw:
        return UNKNOWN_GENRE
    m = re.match(r"^\((\d+)\)", raw)
    if m:
        idx = int(m.group(1))
        if 0 <= idx < len(ID3V1_GENRES):
            return ID3V1_GENRES[idx]
    return raw


def sanitize(name: str, max_len: int = 80) -> str:
    """把字符串处理成 Windows 合法的文件/文件夹名。"""
    cleaned = "".join("_" if c in _INVALID_CHARS else c for c in name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    cleaned = cleaned[:max_len].strip()
    return cleaned or "未知"


def target_path(library: str | Path, info: dict) -> Path:
    """根据标签信息计算目标路径（不触碰文件系统）。"""
    library = Path(library)
    genre = sanitize(normalize_genre(info.get("genre")))
    artist = sanitize(info.get("artist") or UNKNOWN_ARTIST)
    album_raw = (info.get("album") or "").strip()
    album = sanitize(album_raw) if album_raw else ""
    title = sanitize(info.get("title") or Path(info.get("path", "未知")).stem)
    ext = Path(info.get("path", "")).suffix or ".mp3"
    if album:
        return library / genre / artist / f"{artist} - {album}" / f"{title}{ext}"
    return library / genre / artist / f"{title}{ext}"


def organize_file(
    src: str | Path, library: str | Path, info: dict, *, dry_run: bool = False
) -> dict:
    """把文件移动到归类目录；目标已存在时自动加 (1)/(2)… 后缀，绝不覆盖。

    返回 dict：src / dest / status（moved|dry_run）。
    """
    src = Path(src)
    dest = target_path(library, info)
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        i = 1
        while dest.with_name(f"{stem} ({i}){suffix}").exists():
            i += 1
        dest = dest.with_name(f"{stem} ({i}){suffix}")
    result = {"src": str(src), "dest": str(dest), "status": "dry_run"}
    if dry_run:
        return result
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    result["status"] = "moved"
    return result
