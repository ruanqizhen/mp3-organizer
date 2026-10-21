"""MP3 归类到子文件夹，两种模板：

personal（默认）：<音乐库>/<语言.性别>/[<演唱者>/]<演唱者 - 标题>.mp3
    分类由 categories 模块定（现有库学习→规则→大模型→待分类）；
    同演唱者达到阈值或目标下已有该目录时建子目录，否则放分类顶层。
classic：<音乐库>/<类型>/<演唱者>/<演唱者 - 专辑>/<曲名>.mp3
    （无专辑时省去专辑层；缺失的类型/演唱者归入"未知类型"/"未知演唱者"）。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .categories import PENDING_CATEGORY

UNKNOWN_GENRE = "未知类型"
UNKNOWN_ARTIST = "未知演唱者"

TEMPLATE_PERSONAL = "personal"
TEMPLATE_CLASSIC = "classic"

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


# 文件名噪音：括号段里含这些词整段删（注意不是无差别删括号，
# 【胡志明的雨】这种真歌名不在此列）
_FILENAME_NOISE_KEYWORDS = (
    "歌词", "lyric", "video", "mv", "official", "官方",
    "中英", "english", "字幕", "高清", "hd", "免费", "下载",
    "www.", ".com", ".net", "320k", "flac", "无损", "动态",
)

_BRACKET_PAIRS = (("【", "】"), ("[", "]"), ("(", ")"),
                  ("（", "）"), ("「", "」"), ("『", "』"))

_STANDALONE_NOISE_RE = re.compile(
    r"with\s+english|lyric\s+video|中英字幕|动态歌词", re.IGNORECASE)


def _strip_noise_brackets(text: str) -> str:
    """删除含噪音关键词的括号段，如【动态歌词 lyric video】。"""
    for open_c, close_c in _BRACKET_PAIRS:
        pattern = (re.escape(open_c) + r"[^" + re.escape(open_c + close_c)
                   + r"]*" + re.escape(close_c))

        def _repl(m: re.Match) -> str:
            inner = m.group(0)[1:-1].lower()
            if any(k in inner for k in _FILENAME_NOISE_KEYWORDS):
                return ""
            return m.group(0)

        text = re.sub(pattern, _repl, text)
    return text


def clean_display_name(text: str) -> str:
    """清理用于文件名的演唱者/标题显示值。

    去掉噪音括号段（如【动态歌词 lyric video】）、独立噪音词
    （with English、中英字幕等）、末尾孤立的"中英"；统一艺人分隔符
    （" _ "、" / " → "、"）；压缩多余空白。
    注意：只做机械清理。语义级拆分（中英重复、标题混入演唱者等）
    靠大模型校对（--llm-verify），这里不动。
    """
    text = _strip_noise_brackets(text or "")
    text = _STANDALONE_NOISE_RE.sub("", text)
    text = re.sub(r"中英$", "", text)  # 末尾孤立的中英标记，如 "...with English 中英"
    text = text.replace(" _ ", "、").replace(" / ", "、")
    text = re.sub(r"\s+", " ", text).strip(" -_")
    return text


def target_path(library: str | Path, info: dict) -> Path:
    """根据标签信息计算目标路径（不触碰文件系统）。"""
    library = Path(library)
    genre = sanitize(normalize_genre(info.get("genre")))
    artist = sanitize(clean_display_name(info.get("artist") or UNKNOWN_ARTIST))
    album_raw = (info.get("album") or "").strip()
    album = sanitize(clean_display_name(album_raw)) if album_raw else ""
    title = sanitize(clean_display_name(
        info.get("title") or Path(info.get("path", "未知")).stem))
    ext = Path(info.get("path", "")).suffix or ".mp3"
    if album:
        return library / genre / artist / f"{artist} - {album}" / f"{title}{ext}"
    return library / genre / artist / f"{title}{ext}"


def use_artist_subdir(library: str | Path, category: str, artist: str,
                      count: int, threshold: int) -> bool:
    """个人模板：同批同演唱者达到阈值，或目标下已有该演唱者目录 → 用子目录。"""
    artist = (artist or "").strip()
    if not artist:
        return False
    if count >= threshold:
        return True
    return (Path(library) / sanitize(category) / sanitize(artist)).is_dir()


def target_path_personal(library: str | Path, info: dict,
                         category: str, use_subdir: bool) -> Path:
    """个人模板目标路径（不触碰文件系统）。

    <音乐库>/<语言.性别>/[<演唱者>/]<演唱者 - 标题>.mp3；
    无演唱者时文件名只用标题，且不建子目录。
    """
    library = Path(library)
    category = sanitize(category or PENDING_CATEGORY)
    raw_artist = (info.get("artist") or "").strip()
    cleaned_artist = clean_display_name(raw_artist)
    artist = sanitize(cleaned_artist) if cleaned_artist else ""
    title = sanitize(clean_display_name(
        info.get("title") or Path(info.get("path", "未知")).stem))
    ext = Path(info.get("path", "")).suffix or ".mp3"
    filename = f"{artist} - {title}{ext}" if artist else f"{title}{ext}"
    if use_subdir and artist:
        return library / category / artist / filename
    return library / category / filename


def organize_file(
    src: str | Path, library: str | Path, info: dict, *,
    dry_run: bool = False,
    template: str = TEMPLATE_PERSONAL,
    category: str | None = None,
    use_subdir: bool = False,
) -> dict:
    """把文件移动到归类目录；目标已存在时自动加 (1)/(2)… 后缀，绝不覆盖。

    template: personal（默认，语言.性别/演唱者，需调用方定好 category /
              use_subdir）或 classic（类型/演唱者/专辑）。
    返回 dict：src / dest / status（moved|dry_run）/ category。
    """
    src = Path(src)
    if template == TEMPLATE_CLASSIC:
        dest = target_path(library, info)
        category = ""
    else:
        category = category or PENDING_CATEGORY
        dest = target_path_personal(library, info, category, use_subdir)
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        i = 1
        while dest.with_name(f"{stem} ({i}){suffix}").exists():
            i += 1
        dest = dest.with_name(f"{stem} ({i}){suffix}")
    result = {"src": str(src), "dest": str(dest), "status": "dry_run",
              "category": category}
    if dry_run:
        return result
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    result["status"] = "moved"
    return result


def unique_path(dest: Path) -> Path:
    """目标路径已存在时加 (1)/(2)… 后缀，返回可用路径。"""
    dest = Path(dest)
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    i = 1
    while dest.with_name(f"{stem} ({i}){suffix}").exists():
        i += 1
    return dest.with_name(f"{stem} ({i}){suffix}")


def _is_duplicate(src: Path, dest: Path) -> bool:
    """启发式判重：文件大小相同且时长相差 2 秒以内 → 视为同一首歌。

    不同来源的同一首歌编码可能不同，但大小+时长同时撞上的概率极低。
    """
    try:
        if src.stat().st_size != dest.stat().st_size:
            return False
    except OSError:
        return False
    try:
        from mutagen.mp3 import MP3
        a, b = MP3(str(src)), MP3(str(dest))
        return abs(a.info.length - b.info.length) <= 2
    except Exception:  # noqa: BLE001 - 读不出时长时按大小判重
        return True


def merge_file(
    src: str | Path,
    library: str | Path,
    info: dict,
    *,
    on_duplicate: str = "skip",
    dry_run: bool = False,
    template: str = TEMPLATE_PERSONAL,
    category: str | None = None,
    use_subdir: bool = False,
) -> dict:
    """归类并合并到已有音乐库（去重）。

    on_duplicate: "skip"（默认，判重后跳过；非重复则改名并入）、
                  "rename"（直接改名并入）、"replace"（覆盖目标）。
    返回 {"src", "dest", "status"}，
    status: moved | skipped_duplicate | renamed | replaced | dry_run。
    template / category / use_subdir 同 organize_file。
    """
    src = Path(src)
    if template == TEMPLATE_CLASSIC:
        dest = target_path(library, info)
        category = ""
    else:
        category = category or PENDING_CATEGORY
        dest = target_path_personal(library, info, category, use_subdir)
    if dest.exists():
        if on_duplicate == "skip" and _is_duplicate(src, dest):
            action = "skipped_duplicate"
        elif on_duplicate == "replace":
            action = "replaced"
        else:
            dest = unique_path(dest)
            action = "renamed"
    else:
        action = "moved"
    result = {"src": str(src), "dest": str(dest),
              "status": "dry_run" if dry_run else action,
              "action": action, "category": category}
    if dry_run or action == "skipped_duplicate":
        return result
    dest.parent.mkdir(parents=True, exist_ok=True)
    if action == "replaced" and dest.exists():
        dest.unlink()
    shutil.move(str(src), str(dest))
    return result
