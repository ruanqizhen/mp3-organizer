"""命令行入口：mp3tool scan|fix-tags|organize|check|cover|all|gui"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from . import __version__
from .scanner import find_mp3_files, read_info
from .encoding import fix_file, CANDIDATE_ENCODINGS
from .organize import (
    organize_file, use_artist_subdir, TEMPLATE_PERSONAL, TEMPLATE_CLASSIC,
)
from .categories import (
    learn_from_library, classify, normalize_artist, PENDING_CATEGORY,
)
from .integrity import check_file, quarantine
from .cover import process_file
from .report import build_html, build_csv


def _ensure_safe_console_output() -> None:
    """控制台输出容错：GBK 等代码页遇到编不下的字符时降级显示，不抛异常。

    终端打印的只是进度信息，精确的修复对照以 UTF-8 的 HTML/CSV 报告为准。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001 - 输出流形态各异，容错失败也不应中断启动
            continue


def _add_llm_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--llm", action="store_true",
                   help="启用大模型（猜标签、定归类分类时兜底）")
    p.add_argument("--llm-verify", action="store_true",
                   help="大模型优先校对标签：把文件名+现有标签发给大模型验证，"
                        "生成规范的标题/演唱者/专辑/类型/年份（需先启用 --llm）")
    p.add_argument("--llm-base-url", default=None,
                   help="大模型 API 地址（OpenAI 兼容），默认取环境变量 MP3ORGANIZER_LLM_BASE_URL，否则为 DeepSeek")
    p.add_argument("--llm-api-key", default=None,
                   help="API Key（建议用环境变量 MP3ORGANIZER_LLM_API_KEY，避免留在命令行历史里；本地模型可不填）")
    p.add_argument("--llm-model", default=None,
                   help="模型名，默认取环境变量 MP3ORGANIZER_LLM_MODEL，否则为 deepseek-chat")


def _llm_config_from_args(args: argparse.Namespace):
    """根据命令行参数构建 LLMConfig；未启用 --llm 时返回 None。"""
    if not (getattr(args, "llm", False) or getattr(args, "llm_verify", False)):
        return None
    from .llm_tagger import LLMConfig
    cfg = LLMConfig(
        base_url=args.llm_base_url
        or os.environ.get("MP3ORGANIZER_LLM_BASE_URL", "https://api.deepseek.com"),
        api_key=args.llm_api_key
        or os.environ.get("MP3ORGANIZER_LLM_API_KEY", "")
        or os.environ.get("DEEPSEEK_API_KEY", ""),
        model=args.llm_model
        or os.environ.get("MP3ORGANIZER_LLM_MODEL", "deepseek-chat"),
    )
    if not cfg.is_configured():
        raise SystemExit("启用 --llm 需要提供 API 地址和模型名（--llm-base-url / --llm-model）")
    if cfg.needs_key():
        raise SystemExit("请提供 API Key：--llm-api-key，或环境变量 MP3ORGANIZER_LLM_API_KEY"
                         "（本地 Ollama 等本机模型可不填）")
    return cfg


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("root", help="要扫描的 MP3 根目录")
    p.add_argument("--dry-run", action="store_true", help="只报告，不写文件/不移动")
    p.add_argument("--backup-dir", default=None, help="改写前备份原文件的目录")
    p.add_argument("--report", default=None, help="输出 HTML 报告路径")
    p.add_argument("--report-csv", default=None, help="输出 CSV 报告路径")


def _emit_reports(records: list[dict], args: argparse.Namespace) -> None:
    if args.report:
        build_html(records, args.report)
        print(f"HTML 报告: {args.report}")
    if args.report_csv:
        build_csv(records, args.report_csv)
        print(f"CSV 报告: {args.report_csv}")


def cmd_scan(args: argparse.Namespace) -> int:
    files = find_mp3_files(args.root)
    records = [{"path": str(p), **{k: v for k, v in read_info(p).items() if k != "path"}}
               for p in files]
    print(f"扫描到 {len(files)} 个 MP3 文件")
    _emit_reports(
        [{"path": r["path"], "tag_status": "", "tag_changes": [],
          "integrity_status": "", "cover_status": "",
          "new_path": f"{r['artist']} - {r['title']}"} for r in records],
        args,
    )
    return 0


def cmd_fix_tags(args: argparse.Namespace) -> int:
    encodings = tuple(args.encodings.split(",")) if args.encodings else CANDIDATE_ENCODINGS
    llm_config = _llm_config_from_args(args)
    records, n_fixed = [], 0
    for p in find_mp3_files(args.root):
        r = fix_file(p, dry_run=args.dry_run, backup_dir=args.backup_dir,
                     backup_root=args.root, encodings=encodings,
                     force_encoding=args.force_encoding,
                     guess_from_filename=args.guess_from_filename,
                     llm_config=llm_config,
                     llm_verify=getattr(args, "llm_verify", False))
        n_fixed += r["status"] in ("fixed", "verified", "guessed")
        records.append({"path": r["path"], "tag_status": r["status"],
                        "tag_changes": r["changes"], "integrity_status": "",
                        "cover_status": "", "new_path": ""})
        if r["status"] == "fixed":
            print(f"[修复] {p}")
            for c in r["changes"]:
                print(f"    {c['frame']}: {c['old']} → {c['new']} ({c['encoding']})")
        elif r["status"] == "verified":
            print(f"[校对] {p}")
            for c in r["changes"]:
                if c.get("verified"):
                    print(f"    {c['frame']}: {c['old']} → {c['new']}"
                          f" (大模型置信度 {c.get('confidence')})")
                else:
                    print(f"    {c['frame']}: {c['old']} → {c['new']} ({c['encoding']})")
        elif r["status"] == "guessed":
            print(f"[猜测] {p}")
            for c in r["changes"]:
                print(f"    {c['frame']}: → {c['new']} ({c['encoding']})")
        if r.get("llm_error"):
            print(f"  大模型调用失败（已回退）：{r['llm_error']}")
    print(f"共 {len(records)} 个文件，修复 {n_fixed} 个" + ("（试运行）" if args.dry_run else ""))
    _emit_reports(records, args)
    return 0


def cmd_organize(args: argparse.Namespace) -> int:
    infos = [(p, read_info(p)) for p in find_mp3_files(args.root)]
    personal = args.template == TEMPLATE_PERSONAL
    learned = learn_from_library(args.library) if personal else {}
    counts: Counter = Counter()
    if personal:
        for _, info in infos:
            key = normalize_artist(info.get("artist"))
            if key:
                counts[key] += 1
    llm_config = _llm_config_from_args(args)
    records = []
    for p, info in infos:
        kwargs: dict = {"template": args.template}
        if personal:
            category, _, llm_err = classify(info, learned, llm_config=llm_config)
            if llm_err:
                print(f"  大模型分类失败（已放待分类）：{llm_err}")
            key = normalize_artist(info.get("artist"))
            kwargs.update(category=category, use_subdir=use_artist_subdir(
                args.library, category, info.get("artist") or "",
                counts.get(key, 0), args.subdir_threshold))
        r = organize_file(p, args.library, info, dry_run=args.dry_run, **kwargs)
        new_path = r["dest"]
        if r.get("category") == PENDING_CATEGORY:
            new_path += "（待确认分类）"
        records.append({"path": r["src"], "tag_status": "", "tag_changes": [],
                        "integrity_status": "", "cover_status": "",
                        "new_path": new_path})
    print(f"归类 {len(records)} 个文件 → {args.library}" + ("（试运行）" if args.dry_run else ""))
    _emit_reports(records, args)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    records = []
    counts: dict[str, int] = {}
    for p in find_mp3_files(args.root):
        chk = check_file(p, mp3val_exe=args.mp3val, ffmpeg_exe=args.ffmpeg,
                         fix=not args.no_fix)
        counts[chk.status] = counts.get(chk.status, 0) + 1
        new_path = ""
        if chk.status in ("broken", "unreadable") and not args.dry_run and args.quarantine_dir:
            new_path = str(quarantine(p, args.quarantine_dir)) + "（已隔离）"
            print(f"[损坏] {p} → {new_path}")
        elif chk.status == "fixed":
            print(f"[已修复] {p}")
        records.append({"path": chk.path, "tag_status": "", "tag_changes": [],
                        "integrity_status": chk.status + (
                            f"（{'; '.join(chk.details[:2])}）" if chk.details else ""),
                        "cover_status": "", "new_path": new_path})
    print("完整性：", " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    _emit_reports(records, args)
    return 0


def cmd_cover(args: argparse.Namespace) -> int:
    records = []
    for p in find_mp3_files(args.root):
        info = read_info(p)
        r = process_file(p, info, min_score=args.min_score, dry_run=args.dry_run,
                         cache_dir=args.cache_dir, backup_dir=args.backup_dir,
                         backup_root=args.root)
        records.append({"path": r["path"], "tag_status": "", "tag_changes": [],
                        "integrity_status": "", "cover_status": r["status"],
                        "new_path": r.get("reason", "")})
        print(f"[{r['status']}] {p} {r.get('reason', '')}")
    _emit_reports(records, args)
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    """完整流水线：编码修复 → 完整性检查（隔离损坏）→ 归类 → 封面 → 报告。"""
    from .pipeline import run_pipeline, PHASE_NAMES

    encodings = tuple(args.encodings.split(",")) if args.encodings else CANDIDATE_ENCODINGS

    def _progress(i: int, total: int, phase: str, message: str) -> None:
        if phase in ("llm_verify", "llm_classify"):
            print(f"  [大模型 {i}/{total}] {message}")
            return
        if phase in ("tags", "integrity", "organize", "cover"):
            return  # 每个文件只在完成时打印一行
        if phase == "done":
            print(message)

    result = run_pipeline(
        args.root, args.library,
        dry_run=args.dry_run, backup_dir=args.backup_dir,
        quarantine_dir=args.quarantine_dir, cache_dir=args.cache_dir,
        encodings=encodings, force_encoding=args.force_encoding,
        guess_from_filename=args.guess_from_filename,
        llm_config=_llm_config_from_args(args),
        llm_verify=getattr(args, "llm_verify", False),
        template=args.template, subdir_threshold=args.subdir_threshold,
        fix_streams=not args.no_fix, mp3val_exe=args.mp3val, ffmpeg_exe=args.ffmpeg,
        min_score=args.min_score, report_html=args.report, report_csv=args.report_csv,
        progress=_progress,
    )
    for r in result["records"]:
        print(f"[{r.get('tag_status')}/{r.get('integrity_status')}/{r.get('cover_status')}]"
              f" {r['path']}")
        if r.get("llm_error"):
            print(f"  大模型调用失败（已回退）：{r['llm_error']}")
    if args.report:
        print(f"HTML 报告: {args.report}")
    if args.report_csv:
        print(f"CSV 报告: {args.report_csv}")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import main as gui_main
    gui_main()
    return 0


def cmd_rename(args: argparse.Namespace) -> int:
    """把杂乱文件名整理成标准格式；解析出的演唱者/标题顺手填入空标签。"""
    import os
    from .rename import plan_rename
    from .encoding import fill_tags

    llm_config = _llm_config_from_args(args) if args.llm else None
    records, n_renamed = [], 0
    for p in find_mp3_files(args.root):
        plan = plan_rename(p, fmt=args.format, llm_config=llm_config)
        rec = {"path": plan["path"], "tag_status": "", "tag_changes": [],
               "integrity_status": "", "cover_status": "",
               "new_path": plan["new_path"] or plan["reason"]}
        if plan["status"] == "planned":
            if args.dry_run:
                print(f"[试运行改名] {p.name} → {plan['new_name']}（{plan['source']}）")
            else:
                if not args.no_tag_write:
                    fr = fill_tags(
                        p, {"artist": plan["artist"], "title": plan["title"]},
                        source="llm" if plan["source"] == "llm" else "filename",
                        backup_dir=args.backup_dir)
                    rec["tag_status"] = fr["status"]
                    rec["tag_changes"] = fr["changes"]
                os.rename(plan["path"], plan["new_path"])
                n_renamed += 1
                tag_note = f"，标签{rec['tag_status']}" if rec["tag_status"] == "filled" else ""
                print(f"[改名] {p.name} → {plan['new_name']}"
                      f"（{plan['source']}，置信度{plan['confidence']}）{tag_note}")
            if plan.get("llm_error"):
                print(f"  大模型调用失败（已回退正则）：{plan['llm_error']}")
        records.append(rec)
    print(f"共 {len(records)} 个文件，改名 {n_renamed} 个"
          + ("（试运行）" if args.dry_run else ""))
    _emit_reports(records, args)
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """批量整理未整理文件夹，去重合并到已有音乐库（= all + 去重归类）。"""
    from .pipeline import run_pipeline, PHASE_NAMES

    encodings = tuple(args.encodings.split(",")) if args.encodings else CANDIDATE_ENCODINGS

    def _progress(i: int, total: int, phase: str, message: str) -> None:
        if phase in ("llm_verify", "llm_classify"):
            print(f"  [大模型 {i}/{total}] {message}")
            return
        if phase in ("tags", "integrity", "organize", "cover"):
            return
        if phase == "done":
            print(message)

    result = run_pipeline(
        args.root, args.library,
        dry_run=args.dry_run, backup_dir=args.backup_dir,
        quarantine_dir=args.quarantine_dir, cache_dir=args.cache_dir,
        encodings=encodings, force_encoding=args.force_encoding,
        guess_from_filename=args.guess_from_filename,
        llm_config=_llm_config_from_args(args),
        llm_verify=getattr(args, "llm_verify", False),
        template=args.template, subdir_threshold=args.subdir_threshold,
        merge_mode=True, on_duplicate=args.on_duplicate,
        fix_streams=not args.no_fix, mp3val_exe=args.mp3val, ffmpeg_exe=args.ffmpeg,
        min_score=args.min_score, report_html=args.report, report_csv=args.report_csv,
        progress=_progress,
    )
    for r in result["records"]:
        action = r.get("organize_action", r.get("organize_status", ""))
        prefix = "试运行：" if args.dry_run else ""
        if action == "skipped_duplicate":
            print(f"[{prefix}跳过重复] {r['path']}")
        elif action == "renamed":
            print(f"[{prefix}改名并入] {r['path']} → {r['new_path']}")
        elif action == "replaced":
            print(f"[{prefix}覆盖] {r['path']} → {r['new_path']}")
        elif action == "moved":
            print(f"[{prefix}并入] {r['path']} → {r['new_path']}")
        else:
            print(f"[{r.get('tag_status')}/{r.get('integrity_status')}/{r.get('cover_status')}]"
                  f" {r['path']}")
        if r.get("llm_error"):
            print(f"  大模型调用失败（已回退）：{r['llm_error']}")
    if args.report:
        print(f"HTML 报告: {args.report}")
    if args.report_csv:
        print(f"CSV 报告: {args.report_csv}")
    return 0


def _add_template_args(s: argparse.ArgumentParser) -> None:
    """organize/all/merge 共用的归类模板参数。"""
    s.add_argument("--template", default=TEMPLATE_PERSONAL,
                   choices=(TEMPLATE_PERSONAL, TEMPLATE_CLASSIC),
                   help="归类模板：personal 我的方式（语言.性别/演唱者），"
                        "classic 经典方式（类型/演唱者/专辑）")
    s.add_argument("--subdir-threshold", type=int, default=3,
                   help="personal 模板：同演唱者达到该数量才建子目录")


def _add_pipeline_args(s: argparse.ArgumentParser) -> None:
    """all/merge 共用的流水线参数。"""
    _add_common(s)
    s.add_argument("library", help="目标音乐库根目录")
    s.add_argument("--quarantine-dir", default=None, help="损坏文件隔离目录")
    s.add_argument("--no-fix", action="store_true", help="只检查不修复")
    s.add_argument("--mp3val", default="auto")
    s.add_argument("--ffmpeg", default="auto")
    s.add_argument("--encodings", default=None)
    s.add_argument("--force-encoding", default=None)
    s.add_argument("--guess-from-filename", action="store_true",
                   help="无标签时从文件名构建标签；有标签但标题/演唱者为空时补齐")
    _add_llm_args(s)
    _add_template_args(s)
    s.add_argument("--min-score", type=float, default=0.75)
    s.add_argument("--cache-dir", default=None)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mp3tool", description="Windows 老 MP3 批量整理工具")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="扫描并列出 MP3 与标签")
    _add_common(s)

    s = sub.add_parser("fix-tags", help="修复标签编码为 Unicode")
    _add_common(s)
    s.add_argument("--encodings", default=None, help="尝试顺序，如 gbk,big5")
    s.add_argument("--force-encoding", default=None, help="强制指定编码，如 gbk")
    s.add_argument("--guess-from-filename", action="store_true",
                   help="无标签时从文件名构建标签；有标签但标题/演唱者为空时补齐")
    _add_llm_args(s)

    s = sub.add_parser("organize", help="按语言.性别/演唱者归类到音乐库")
    _add_common(s)
    s.add_argument("library", help="目标音乐库根目录")
    _add_template_args(s)
    _add_llm_args(s)

    s = sub.add_parser("check", help="完整性检查/修复/隔离")
    _add_common(s)
    s.add_argument("--quarantine-dir", default=None, help="损坏文件隔离目录")
    s.add_argument("--no-fix", action="store_true", help="只检查不修复")
    s.add_argument("--mp3val", default="auto", help="mp3val 路径，auto 自动查找，留空跳过")
    s.add_argument("--ffmpeg", default="auto", help="ffmpeg 路径，auto 自动查找，留空跳过")

    s = sub.add_parser("cover", help="搜索并嵌入专辑封面")
    _add_common(s)
    s.add_argument("--min-score", type=float, default=0.75, help="自动嵌入的最低匹配分")
    s.add_argument("--cache-dir", default=None, help="封面下载缓存目录")

    s = sub.add_parser("all", help="完整流水线")
    _add_pipeline_args(s)

    s = sub.add_parser("merge", help="批量整理未整理文件夹并去重合并到已有音乐库")
    _add_pipeline_args(s)
    s.add_argument("--on-duplicate", default="skip",
                   choices=("skip", "rename", "replace"),
                   help="目标已存在同曲目时的处理：skip 跳过重复（默认），"
                        "rename 改名并入，replace 覆盖")

    s = sub.add_parser("rename", help="把杂乱文件名整理成标准格式（默认“演唱者 - 标题”）")
    _add_common(s)
    s.add_argument("--format", default="{artist} - {title}",
                   help="标准文件名模板，可用 {artist}/{title}/{album}，默认“{artist} - {title}”")
    _add_llm_args(s)
    s.add_argument("--no-tag-write", action="store_true",
                   help="只改文件名，不写标签")

    s = sub.add_parser("gui", help="启动图形界面")
    return p


def main(argv: list[str] | None = None) -> int:
    _ensure_safe_console_output()
    args = build_parser().parse_args(argv)
    # 空字符串表示“跳过该工具”
    for key in ("mp3val", "ffmpeg"):
        if hasattr(args, key) and getattr(args, key) == "":
            setattr(args, key, None)
    return {
        "scan": cmd_scan, "fix-tags": cmd_fix_tags, "organize": cmd_organize,
        "check": cmd_check, "cover": cmd_cover, "all": cmd_all, "gui": cmd_gui,
        "rename": cmd_rename, "merge": cmd_merge,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
