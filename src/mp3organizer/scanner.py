"""递归扫描 MP3 文件并读取基础标签信息。"""
from __future__ import annotations

from pathlib import Path

from mutagen.id3 import ID3

MP3_SUFFIXES = {".mp3"}


def find_mp3_files(root: str | Path) -> list[Path]:
    """递归找出目录下所有 .mp3 文件（按路径排序）。"""
    root = Path(root)
    return sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in MP3_SUFFIXES
    )


def _first_text(audio: ID3, frame_id: str) -> str:
    frame = audio.get(frame_id)
    if frame is None:
        return ""
    text = getattr(frame, "text", None)
    if isinstance(text, list) and text:
        value = text[0]
        return value if isinstance(value, str) else str(value)
    return ""


def read_info(path: str | Path) -> dict:
    """读取标题/演唱者/专辑/类型/年份。

    读不到（无标签或文件损坏）时返回空字符串，不抛异常。
    """
    path = Path(path)
    info = {
        "title": "",
        "artist": "",
        "album": "",
        "genre": "",
        "year": "",
        "path": str(path),
    }
    try:
        audio = ID3(str(path))
    except Exception:
        return info
    info["title"] = _first_text(audio, "TIT2") or path.stem
    info["artist"] = _first_text(audio, "TPE1")
    info["album"] = _first_text(audio, "TALB")
    info["genre"] = _first_text(audio, "TCON")
    info["year"] = _first_text(audio, "TDRC") or _first_text(audio, "TYER")
    return info


def display_relpath(path: str | Path, root: str | Path | None) -> str:
    """相对路径（POSIX 风格，供大模型参考）；算不出时退回"父目录/文件名"。"""
    p = Path(path)
    if root:
        try:
            return p.relative_to(Path(root)).as_posix()
        except ValueError:
            pass
    parent = p.parent.name
    return f"{parent}/{p.name}" if parent else p.name
