"""可复用的处理流水线：CLI 与 GUI 共用同一套逻辑。

run_pipeline() 按顺序执行：扫描 → 标签修复 → 完整性检查 → 归类整理 →
专辑封面 → 报告。各步骤可单独开关。progress 回调用于界面进度显示。
"""
from __future__ import annotations

from pathlib import Path

from .scanner import find_mp3_files, read_info
from .encoding import fix_file, CANDIDATE_ENCODINGS
from .organize import organize_file, merge_file
from .integrity import check_file, quarantine
from .cover import process_file
from .report import build_html, build_csv

PHASE_NAMES = {
    "scan": "扫描",
    "tags": "修复标签",
    "integrity": "完整性检查",
    "organize": "归类整理",
    "cover": "专辑封面",
    "report": "生成报告",
    "done": "完成",
}


def run_pipeline(
    root: str | Path,
    library: str | Path | None = None,
    *,
    dry_run: bool = False,
    backup_dir: str | Path | None = None,
    quarantine_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    do_tags: bool = True,
    do_check: bool = True,
    do_organize: bool = True,
    do_cover: bool = True,
    encodings: tuple[str, ...] = CANDIDATE_ENCODINGS,
    force_encoding: str | None = None,
    guess_from_filename: bool = False,
    fix_streams: bool = True,
    mp3val_exe: str | None = "auto",
    ffmpeg_exe: str | None = "auto",
    min_score: float = 0.75,
    report_html: str | Path | None = None,
    report_csv: str | Path | None = None,
    llm_config=None,
    llm_cache_dir: str | Path | None = None,
    merge_mode: bool = False,
    on_duplicate: str = "skip",
    progress=None,
) -> dict:
    """执行完整流水线，返回 {"records", "total", "counts"}。

    progress: 可选回调 progress(index, total, phase, message)，
    phase 取值见 PHASE_NAMES 的键。
    """
    if do_organize and not library:
        raise ValueError("启用归类整理时必须指定 library（音乐库目录）")

    def _p(i: int, total: int, phase: str, message: str = "") -> None:
        if progress is not None:
            progress(i, total, phase, message)

    root = Path(root)
    files = find_mp3_files(root)
    total = len(files)
    _p(0, total, "scan", f"扫描到 {total} 个 MP3 文件")

    records: list[dict] = []
    for i, p in enumerate(files, 1):
        rec: dict = {"path": str(p), "tag_changes": [], "new_path": ""}
        info = read_info(p)

        # 1. 标签修复
        if do_tags:
            _p(i, total, "tags", str(p))
            tag = fix_file(
                p, dry_run=dry_run, backup_dir=backup_dir, backup_root=root,
                encodings=encodings, force_encoding=force_encoding,
                guess_from_filename=guess_from_filename,
                llm_config=llm_config,
                llm_cache_dir=llm_cache_dir or cache_dir,
            )
            rec["tag_status"] = tag["status"]
            rec["tag_changes"] = tag["changes"]
            if tag.get("llm_error"):
                rec["llm_error"] = tag["llm_error"]
            info = read_info(p)  # 用修复后的标签继续
        else:
            rec["tag_status"] = "skipped"

        # 2. 完整性检查
        if do_check:
            _p(i, total, "integrity", str(p))
            chk = check_file(p, mp3val_exe=mp3val_exe, ffmpeg_exe=ffmpeg_exe,
                             fix=fix_streams)
            rec["integrity_status"] = chk.status
        else:
            chk = None
            rec["integrity_status"] = "skipped"

        if chk is not None and chk.status in ("broken", "unreadable"):
            if not dry_run and quarantine_dir:
                rec["new_path"] = str(quarantine(p, quarantine_dir)) + "（已隔离）"
            rec["cover_status"] = "skipped"
            records.append(rec)
            continue

        # 3. 归类整理
        target = p
        if do_organize:
            _p(i, total, "organize", str(p))
            if merge_mode:
                # 合并到已有音乐库：目标已存在时按 on_duplicate 去重处理
                org = merge_file(p, library, info, on_duplicate=on_duplicate,
                                 dry_run=dry_run)
            else:
                org = organize_file(p, library, info, dry_run=dry_run)
            rec["new_path"] = org["dest"]
            rec["organize_status"] = org["status"]
            rec["organize_action"] = org.get("action", org["status"])
            if not dry_run:
                target = Path(org["dest"])

        # 4. 专辑封面
        if do_cover:
            _p(i, total, "cover", str(target))
            cov = process_file(
                target, info, min_score=min_score, dry_run=dry_run,
                cache_dir=cache_dir, backup_dir=backup_dir,
                backup_root=library if do_organize else root,
            )
            rec["cover_status"] = cov["status"]
            if cov.get("reason"):
                rec["cover_status"] += f"（{cov['reason']}）"
        else:
            rec["cover_status"] = "skipped"

        records.append(rec)

    _p(total, total, "report", "生成报告")
    if report_html:
        build_html(records, report_html)
    if report_csv:
        build_csv(records, report_csv)
    _p(total, total, "done", f"处理完成：{len(records)} 个文件")

    counts: dict[str, int] = {}
    for r in records:
        for key in ("tag_status", "integrity_status", "cover_status"):
            base = str(r.get(key, "")).split("（")[0]  # 去掉备注括号，只按基础状态计数
            k = f"{key}={base}"
            counts[k] = counts.get(k, 0) + 1
    return {"records": records, "total": total, "counts": counts}
