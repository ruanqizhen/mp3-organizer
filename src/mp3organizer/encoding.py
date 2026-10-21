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

from mutagen.id3 import ID3, ID3NoHeaderError, Encoding, TIT2, TPE1, TALB, TRCK, TCON, TDRC

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


def _apply_guess(audio: ID3, guess: dict, source: str = "filename") -> list[dict]:
    """把猜出的标签写入 audio（只填空字段，不覆盖已有值）。

    source: "filename"（正则）或 "llm"（大模型），记入 changes 供报告标注。
    返回 changes 记录，每条带 guessed=True 标记。
    """
    changes = []
    mapping = (("TIT2", "title"), ("TPE1", "artist"), ("TALB", "album"),
               ("TCON", "genre"), ("TDRC", "year"), ("TRCK", "track"))
    for frame_id, key in mapping:
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
        cls = {"TIT2": TIT2, "TPE1": TPE1, "TALB": TALB,
               "TCON": TCON, "TDRC": TDRC, "TRCK": TRCK}[frame_id]
        audio.add(cls(encoding=Encoding.UTF8, text=[value]))
        changes.append({"frame": frame_id, "old": "", "new": value,
                        "encoding": "llm" if source == "llm" else "filename",
                        "guessed": True,
                        "confidence": guess.get("confidence")})
    return changes


def _frame_text(audio: ID3, frame_id: str) -> str:
    """取文本帧的首个字符串（去首尾空格），缺失时返回空字符串。

    mutagen 时间戳帧（TDRC 等）的 text[0] 是 ID3TimeStamp 而非 str，
    这里统一转成 str。
    """
    frame = audio.get(frame_id)
    if frame is None:
        return ""
    text = getattr(frame, "text", None)
    if isinstance(text, list) and text:
        v = text[0]
        if isinstance(v, str):
            return v.strip()
        try:
            return str(v).strip()
        except Exception:  # noqa: BLE001 - 转字符串失败就当空
            return ""
    return ""


def _apply_verify(audio: ID3, verified: dict) -> list[dict]:
    """把大模型校对后的标签写入 audio（覆盖式）。

    只动 LLM 给出非空且与现有不同的字段；LLM 没给出的字段保留原值，
    避免把已有的正确信息洗掉。返回 changes 记录，每条带 verified=True、
    confidence 与 note，供报告标注。
    """
    changes = []
    mapping = (("TIT2", "title"), ("TPE1", "artist"), ("TALB", "album"),
               ("TCON", "genre"), ("TDRC", "year"), ("TRCK", "track"))
    cls_map = {"TIT2": TIT2, "TPE1": TPE1, "TALB": TALB,
               "TCON": TCON, "TDRC": TDRC, "TRCK": TRCK}
    for frame_id, key in mapping:
        new_value = (verified.get(key) or "").strip()
        if not new_value:
            continue
        current = _frame_text(audio, frame_id)
        if current == new_value:
            continue
        if frame_id in audio:
            del audio[frame_id]
        audio.add(cls_map[frame_id](encoding=Encoding.UTF8, text=[new_value]))
        changes.append({"frame": frame_id, "old": current, "new": new_value,
                        "encoding": "llm_verify", "verified": True,
                        "confidence": verified.get("confidence"),
                        "note": verified.get("note", "")})
    return changes


def _build_tag_from_filename(
    path: Path, result: dict, *, dry_run: bool,
    backup_dir: str | Path | None, backup_root: str | Path | None,
    llm_config=None, llm_cache_dir: str | Path | None = None, _llm_fn=None,
    llm_verify: bool = False, llm_verify_result: dict | None = None,
    _verify_fn=None,
) -> dict:
    """文件完全没有标签时，从文件名构建一个新标签。

    llm_verify=True 且大模型可用时：优先用大模型校对（文件名+空标签），
    拿不到可用结果才回退到正则→大模型两档。
    文件名本身也可能乱码（如 GBK 被误读），先修再送大模型/正则。
    """
    # 文件名乱码先修，否则大模型看到的是 °®ÀÖÍÅ 这类垃圾
    display_name, _ = fix_text(path.name)
    if llm_verify and llm_config is not None and llm_config.is_configured():
        verified = llm_verify_result
        if verified is None:
            try:
                from .llm_tagger import verify_tags
                fn = _verify_fn or verify_tags
                verified = fn(
                    [{"filename": display_name, "title": "", "artist": "",
                      "album": "", "genre": "", "year": "", "track": ""}],
                    llm_config, cache_dir=llm_cache_dir)[0]
            except Exception as exc:  # noqa: BLE001 - 失败就回退正则
                result["llm_error"] = "标签校对失败：%s: %s" % (
                    type(exc).__name__, exc)
                verified = None
        if verified and (verified.get("title") or verified.get("artist")):
            audio = ID3()
            result["changes"] = _apply_verify(audio, verified)
            result["status"] = "verified"
            if dry_run:
                return result
            if backup_dir is not None:
                result["backup"] = str(
                    _backup_copy(path, Path(backup_dir), backup_root))
            audio.save(str(path), v2_version=4)
            return result
    from .filename_tags import guess_tags

    guess, source, llm_error = guess_tags(
        path, llm_config=llm_config, llm_cache_dir=llm_cache_dir, _llm_fn=_llm_fn)
    if llm_error:
        result["llm_error"] = llm_error
    if not guess["title"] and not guess["artist"]:
        result["status"] = "no_tag"
        result["error"] = "文件名无法解析出标题/演唱者"
        return result
    audio = ID3()
    result["changes"] = _apply_guess(audio, guess, source)
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
    llm_config=None,
    llm_cache_dir: str | Path | None = None,
    _llm_fn=None,
    llm_verify: bool = False,
    llm_verify_result: dict | None = None,
    _verify_fn=None,
) -> dict:
    """修复单个 MP3 的标签编码。

    返回 dict：path / status（fixed|verified|guessed|unchanged|no_tag|unreadable）/
    changes（[{frame, old, new, encoding[, guessed|verified[, confidence[, note]]]}]）/
    backup / error / llm_error（大模型调用失败时）。
    dry_run=True 时只报告不写文件；backup_dir 指定时改写前先备份。
    guess_from_filename=True 时：无标签则从文件名构建；有标签但标题/
    演唱者为空时，从文件名补齐空字段（不覆盖已有值）。
    llm_config 传入后，正则猜不出时回退到大模型（_llm_fn 供测试注入）。
    llm_verify=True 且大模型可用时：编码修复后，优先把文件名+现有标签
    发给大模型做整体校对，用返回的规范标签覆盖（_verify_fn 供测试注入；
    llm_verify_result 可传入预批量查询的结果，避免重复请求）。
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
        if guess_from_filename or llm_verify:
            return _build_tag_from_filename(
                path, result, dry_run=dry_run,
                backup_dir=backup_dir, backup_root=backup_root,
                llm_config=llm_config, llm_cache_dir=llm_cache_dir,
                _llm_fn=_llm_fn,
                llm_verify=llm_verify,
                llm_verify_result=llm_verify_result,
                _verify_fn=_verify_fn,
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

    verify_applied = False
    if llm_verify and llm_config is not None and llm_config.is_configured():
        # 大模型优先校对：文件名 + 编码修复后的标签 → 规范标签（覆盖式）
        item = {
            "filename": path.name,
            "title": _frame_text(audio, "TIT2"),
            "artist": _frame_text(audio, "TPE1"),
            "album": _frame_text(audio, "TALB"),
            "genre": _frame_text(audio, "TCON"),
            "year": _frame_text(audio, "TDRC"),
            "track": _frame_text(audio, "TRCK"),
        }
        verified = llm_verify_result
        if verified is None:
            try:
                from .llm_tagger import verify_tags
                fn = _verify_fn or verify_tags
                verified = fn([item], llm_config,
                              cache_dir=llm_cache_dir)[0]
            except Exception as exc:  # noqa: BLE001 - 失败就回退正则
                result["llm_error"] = "标签校对失败：%s: %s" % (
                    type(exc).__name__, exc)
                verified = None
        if verified:
            vchanges = _apply_verify(audio, verified)
            if vchanges:
                result["changes"].extend(vchanges)
                verify_applied = True

    if not verify_applied and not result["changes"] and guess_from_filename:
        # 标签存在但标题/演唱者等为空，从文件名补齐（不覆盖已有值）
        from .filename_tags import guess_tags

        guess, source, llm_error = guess_tags(
            path, llm_config=llm_config, llm_cache_dir=llm_cache_dir,
            _llm_fn=_llm_fn)
        if llm_error:
            result["llm_error"] = llm_error
        filled = _apply_guess(audio, guess, source)
        if filled:
            result["changes"] = filled

    if not result["changes"]:
        return result
    result["status"] = "verified" if verify_applied else "fixed"
    if dry_run:
        return result
    if backup_dir is not None:
        result["backup"] = str(_backup_copy(path, Path(backup_dir), backup_root))
    audio.save(v2_version=4)  # 统一写回 ID3v2.4 + UTF-8
    return result


def fill_tags(
    path: str | Path,
    fields: dict,
    *,
    source: str = "filename",
    dry_run: bool = False,
    backup_dir: str | Path | None = None,
) -> dict:
    """把 fields 中的标签值填入空字段（不覆盖已有值）。

    fields 可含 title/artist/album/genre/year/track 子集。
    返回 {"status": "filled"|"unchanged"|"error", "changes": [...],
          "backup": ..., "error": ...}。
    """
    path = Path(path)
    try:
        try:
            audio = ID3(str(path))
        except ID3NoHeaderError:
            audio = ID3()
        changes = _apply_guess(audio, fields, source)
        if not changes:
            return {"status": "unchanged", "changes": [], "backup": None,
                    "error": None}
        if dry_run:
            return {"status": "filled", "changes": changes, "backup": None,
                    "error": None}
        backup = None
        if backup_dir is not None:
            backup = str(_backup_copy(path, Path(backup_dir)))
        audio.save(str(path), v2_version=4)
        return {"status": "filled", "changes": changes, "backup": backup,
                "error": None}
    except Exception as exc:  # noqa: BLE001 - 标签损坏形态各异，统一记录
        return {"status": "error", "changes": [], "backup": None,
                "error": "%s: %s" % (type(exc).__name__, exc)}
