"""MP3 完整性检查：能修的修，修不好的标记隔离。

三级流水线：
  1. mutagen 加载 —— 连标签/文件头都解析失败，直接判 unreadable；
  2. mp3val（若可用）—— 检测 MPEG 帧错误，-f 参数可修复一部分；
  3. ffmpeg（若可用）—— 全程解码，任何 error 即判 broken（最终裁决）。

状态：ok（正常）| fixed（已修复）| broken（损坏）| unreadable（无法解析）
     | skipped（缺少检测工具，未检查）
修不好的文件用 quarantine() 改名 “[损坏]” 并移入隔离目录。
各检查器可通过 runners 参数注入假实现，便于测试。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from mutagen.mp3 import MP3


@dataclass
class CheckResult:
    path: str
    status: str = "ok"
    details: list[str] = field(default_factory=list)


def _run(cmd: list[str], timeout: int = 180) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"找不到可执行文件: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"执行超时（>{timeout}s）: {' '.join(cmd)}"


def _mutagen_check(path: Path) -> tuple[bool, str]:
    try:
        MP3(str(path))
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"mutagen 无法解析: {type(exc).__name__}: {exc}"


def _parse_mp3val_output(text: str) -> tuple[list[str], list[str]]:
    problems, fixed = [], []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("FIXED:"):
            fixed.append(s)
        elif s.startswith(("WARNING:", "ERROR:")):
            problems.append(s)
    return problems, fixed


def _mp3val_check(path: Path, exe: str, fix: bool, timeout: int) -> tuple[str, list[str]]:
    cmd = [exe]
    if fix:
        cmd.append("-f")
    cmd += ["-nb", str(path)]
    _, out, err = _run(cmd, timeout)
    problems, fixed = _parse_mp3val_output(out + "\n" + err)
    details = fixed + problems
    if fixed and not problems:
        return "fixed", details or ["mp3val 修复完成"]
    if problems:
        return "broken", details
    if fixed:
        return "fixed", details
    return "ok", []


def _ffmpeg_check(path: Path, exe: str, timeout: int) -> tuple[str, list[str]]:
    cmd = [exe, "-v", "error", "-i", str(path), "-f", "null", "-"]
    _, _, err = _run(cmd, timeout)
    lines = [ln.strip() for ln in err.splitlines() if ln.strip()]
    # 过滤掉纯提示行，保留真正的错误
    errors = [ln for ln in lines if not ln.startswith("ffmpeg version")]
    if errors:
        return "broken", errors[:10]
    return "ok", []


def check_file(
    path: str | Path,
    *,
    mp3val_exe: str | None = "auto",
    ffmpeg_exe: str | None = "auto",
    fix: bool = True,
    timeout: int = 180,
    runners: dict | None = None,
) -> CheckResult:
    """检查单个 MP3 文件的完整性。

    mp3val_exe / ffmpeg_exe：可执行文件路径；"auto" 自动查找；
    None 则跳过该级检查。fix=False 时 mp3val 只扫描不修复。
    runners：{"mutagen": fn, "mp3val": fn, "ffmpeg": fn} 注入假实现，
    供测试使用（fn 返回 (ok_bool, details) 或 (status, details)）。
    """
    runners = runners or {}
    path = Path(path)
    res = CheckResult(path=str(path))

    # L1: mutagen 粗检
    mutagen_check = runners.get("mutagen", _mutagen_check)
    ok, msg = mutagen_check(path)
    if not ok:
        res.status = "unreadable"
        res.details.append(msg)
        return res

    if mp3val_exe == "auto":
        mp3val_exe = shutil.which("mp3val")
    if ffmpeg_exe == "auto":
        ffmpeg_exe = shutil.which("ffmpeg")

    if mp3val_exe is None and ffmpeg_exe is None:
        res.status = "skipped"
        res.details.append("未找到 mp3val/ffmpeg，未做流检查")
        return res

    worst = "ok"
    # L2: mp3val
    if mp3val_exe:
        mp3val_check = runners.get("mp3val")
        if mp3val_check is not None:
            status, details = mp3val_check(path)
        else:
            status, details = _mp3val_check(path, mp3val_exe, fix, timeout)
        res.details.extend(f"mp3val: {d}" for d in details)
        if status == "broken":
            worst = "broken"
        elif status == "fixed" and worst == "ok":
            worst = "fixed"
    # L3: ffmpeg 终检
    if ffmpeg_exe:
        ffmpeg_check = runners.get("ffmpeg")
        if ffmpeg_check is not None:
            status, details = ffmpeg_check(path)
        else:
            status, details = _ffmpeg_check(path, ffmpeg_exe, timeout)
        res.details.extend(f"ffmpeg: {d}" for d in details)
        if status == "broken":
            worst = "broken"
    res.status = worst
    return res


def quarantine(src: str | Path, quarantine_dir: str | Path) -> Path:
    """把损坏文件改名加 “[损坏]” 后缀并移入隔离目录，返回新路径。"""
    src = Path(src)
    quarantine_dir = Path(quarantine_dir)
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    dest = quarantine_dir / f"{src.stem}[损坏]{src.suffix}"
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        i = 1
        while dest.with_name(f"{stem} ({i}){suffix}").exists():
            i += 1
        dest = dest.with_name(f"{stem} ({i}){suffix}")
    shutil.move(str(src), str(dest))
    return dest
