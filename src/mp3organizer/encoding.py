"""MP3 元数据编码修复。

老 Windows 中文 MP3 的典型问题：
  * ID3v2.3 标签帧声明编码为 latin1，实际字节却是 GBK/Big5；
  * ID3v1 根本没有编码概念，中文 Windows 直接写入 GBK 字节。
mutagen 会把这两类都读成 latin1 解码后的乱码字符串（mutagen 1.48 起
会自动把 ID3v1 尾解析成 v2 帧，所以统一按帧处理即可）。

修复思路：对疑似乱码的字符串，用 latin1 编码取回原始字节，再依次尝试
gbk / big5 / shift_jis / euc-kr / utf-8 解码；第一个能完整解码、且结果
含东亚文字的即采用。修复后统一写回 ID3v2.4 + UTF-8。

自动模式针对中文收藏做了启发式过滤（高位字节占比 + 结果含东亚文字），
以降低把西欧文字（如 café）误转的风险；日韩收藏可用 encodings 参数
调整尝试顺序，或用 force_encoding 强制指定。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError, Encoding, TIT2, TPE1, TALB, TRCK

CANDIDATE_ENCODINGS: tuple[str, ...] = ("gbk", "big5", "shift_jis", "euc-kr", "utf-8")


def _contains_east_asian(text: str) -> bool:
    """是否包含 CJK / 假名 / 谚文字符。"""
    for ch in text:
        o = ord(ch)
        if (
            0x4E00 <= o <= 0x9FFF  # CJK 统一表意文字
            or 0x3400 <= o <= 0x4DBF  # CJK 扩展 A
            or 0xF900 <= o <= 0xFAFF  # CJK 兼容表意文字
            or 0x3040 <= o <= 0x30FF  # 平假名/片假名
            or 0xAC00 <= o <= 0xD7AF  # 谚文音节
        ):
            return True
    return False


def fix_text(
    text: str,
    encodings: tuple[str, ...] = CANDIDATE_ENCODINGS,
    force: str | None = None,
) -> tuple[str, str | None]:
    """尝试修复一条疑似乱码的文本。

    返回 (修复后文本, 用到的编码)；无需修复时返回 (原文, None)。
    force 指定编码时跳过启发式过滤，直接尝试解码。
    """
    if not text or not isinstance(text, str):
        return text, None
    try:
        raw = text.encode("latin1")
    except (UnicodeEncodeError, ValueError):
        return text, None  # 已是真正的 Unicode（ latin1 装不下），原样保留
    if not any(b >= 0x80 for b in raw):
        return text, None  # 纯 ASCII，无需修复

    order = (force,) if force else encodings
    if force:
        for enc in order:
            try:
                decoded = raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
            if decoded != text:
                return decoded, enc
        return text, None

    # 自动模式：启发式过滤，降低误伤西欧文字的概率
    high = sum(1 for b in raw if b >= 0x80)
    if high < 2 or high / len(raw) < 0.35:
        return text, None
    for enc in order:
        try:
            decoded = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if decoded != text and _contains_east_asian(decoded):
            return decoded, enc
    return text, None


def _backup_copy(path: Path, backup_dir: Path, backup_root: Path | None = None) -> Path:
    """把原文件复制到备份目录（尽量保持相对目录结构），返回备份路径。"""
    if backup_root is not None:
        try:
            rel = path.resolve().relative_to(Path(backup_root).resolve())
        except ValueError:
            rel = None
    else:
        rel = None
    if rel is None:
        parts = path.resolve().parts
        rel = Path(*parts[1:]) if len(parts) > 1 else Path(parts[0])
    dest = backup_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():  # 避免覆盖已有备份
        stem, suffix = dest.stem, dest.suffix
        i = 1
        while dest.with_name(f"{stem}.bak{i}{suffix}").exists():
            i += 1
        dest = dest.with_name(f"{stem}.bak{i}{suffix}")
    shutil.copy2(path, dest)
    return dest


def _apply_guess(audio: ID3, guess: dict) -> list[dict]:
    """把从文件名猜出的标签写入 audio（只填空字段，不覆盖已有值）。

    返回 changes 记录，每条带 guessed=True 标记。
    """
    changes = []
    for frame_id, key in (("TIT2", "title"), ("TPE1", "artist"),
                          ("TALB", "album"), ("TRCK", "track")):
        value = (guess.get(key) or "").strip()
        if not value:
            continue
        frame = audio.get(frame_id)
        current = ""
        if frame is not None:
            text = getattr(frame, "text", None)
            if isinstance(text, list) and text and isinstance(text[0], str):
                current = text[0].strip()
        if current:
            continue
        cls = {"TIT2": TIT2, "TPE1": TPE1, "TALB": TALB, "TRCK": TRCK}[frame_id]
        audio.add(cls(encoding=Encoding.UTF8, text=[value]))
        changes.append({"frame": frame_id, "old": "", "new": value,
                        "encoding": "filename", "guessed": True})
    return changes


def _build_tag_from_filename(
    path: Path, result: dict, *, dry_run: bool,
    backup_dir: str | Path | None, backup_root: str | Path | None,
) -> dict:
    """文件完全没有标签时，从文件名构建一个新标签。"""
    from .filename_tags import guess_from_filename as _guess

    guess = _guess(path)
    if not guess["title"] and not guess["artist"]:
        result["status"] = "no_tag"
        result["error"] = "文件名无法解析出标题/演唱者"
        return result
    audio = ID3()
    result["changes"] = _apply_guess(audio, guess)
    result["status"] = "guessed"
    if dry_run:
        return result
    if backup_dir is not None:
        result["backup"] = str(_backup_copy(path, Path(backup_dir), backup_root))
    audio.save(str(path), v2_version=4)
    return result


def fix_file(
    path: str | Path,
    *,
    dry_run: bool = False,
    backup_dir: str | Path | None = None,
    backup_root: str | Path | None = None,
    encodings: tuple[str, ...] = CANDIDATE_ENCODINGS,
    force_encoding: str | None = None,
    guess_from_filename: bool = False,
) -> dict:
    """修复单个 MP3 的标签编码。

    返回 dict：path / status（fixed|guessed|unchanged|no_tag|unreadable）/
    changes（[{frame, old, new, encoding[, guessed]}]）/ backup / error。
    dry_run=True 时只报告不写文件；backup_dir 指定时改写前先备份。
    guess_from_filename=True 时：无标签则从文件名构建；有标签但标题/
    演唱者为空时，从文件名补齐空字段（不覆盖已有值）。
    """
    path = Path(path)
    result: dict = {
        "path": str(path),
        "status": "unchanged",
        "changes": [],
        "backup": None,
        "error": None,
    }
    try:
        audio = ID3(str(path))
    except ID3NoHeaderError:
        if guess_from_filename:
            return _build_tag_from_filename(
                path, result, dry_run=dry_run,
                backup_dir=backup_dir, backup_root=backup_root,
            )
        result["status"] = "no_tag"
        return result
    except Exception as exc:  # noqa: BLE001 - 标签损坏形态各异，统一记录
        result["status"] = "unreadable"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    for frame_id, frame in list(audio.items()):
        text = getattr(frame, "text", None)
        if not isinstance(text, list):
            continue
        new_text: list = []
        changed = False
        for item in text:
            if not isinstance(item, str):
                new_text.append(item)
                continue
            if (
                not force_encoding
                and getattr(frame, "encoding", Encoding.LATIN1) != Encoding.LATIN1
            ):
                new_text.append(item)  # 已声明为 UTF-16/UTF-8 的帧不动
                continue
            fixed, enc = fix_text(item, encodings=encodings, force=force_encoding)
            if enc:
                result["changes"].append(
                    {"frame": frame_id, "old": item, "new": fixed, "encoding": enc}
                )
                changed = True
            new_text.append(fixed)
        if changed:
            frame.text = new_text
            frame.encoding = Encoding.UTF8

    if not result["changes"] and guess_from_filename:
        # 标签存在但标题/演唱者等为空，从文件名补齐（不覆盖已有值）
        from .filename_tags import guess_from_filename as _guess

        filled = _apply_guess(audio, _guess(path))
        if filled:
            result["changes"] = filled

    if not result["changes"]:
        return result
    result["status"] = "fixed"
    if dry_run:
        return result
    if backup_dir is not None:
        result["backup"] = str(_backup_copy(path, Path(backup_dir), backup_root))
    audio.save(v2_version=4)  # 统一写回 ID3v2.4 + UTF-8
    return result
