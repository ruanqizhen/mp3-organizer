"""可复用的处理流水线：CLI 与 GUI 共用同一套逻辑。

run_pipeline() 按文件逐个处理：对一个文件依次做完标签修复 → 完整性检查 →
归类整理 → 专辑封面，再处理下一个文件。各步骤可单独开关。
progress 回调用于界面进度显示。

大模型调用在主循环前预批量一次（标签校对、归类分类），避免逐文件
请求；源文件夹中文件搬走后，沿途变空的子目录会被删除。
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .scanner import find_mp3_files, read_info
from .encoding import fix_file, CANDIDATE_ENCODINGS
from .organize import (
    organize_file, merge_file, TEMPLATE_PERSONAL, use_artist_subdir,
)
from .categories import (
    learn_from_library, classify, normalize_artist, PENDING_CATEGORY,
    guess_by_rules,
)
from .integrity import check_file, quarantine
from .cover import process_file
from .report import build_html, build_csv

PHASE_NAMES = {
    "scan": "扫描",
    "llm_verify": "大模型校对标签",
    "llm_classify": "大模型归类分类",
    "tags": "修复标签",
    "integrity": "完整性检查",
    "organize": "归类整理",
    "cover": "专辑封面",
    "report": "生成报告",
    "done": "完成",
}


_TAG_FIELDS = {"TIT2": "title", "TPE1": "artist", "TALB": "album",
               "TCON": "genre", "TDRC": "year", "TRCK": "track"}

_VERIFY_FIELDS = ("filename", "title", "artist", "album", "genre",
                  "year", "track")


def _apply_tag_changes(info: dict, changes: list[dict]) -> dict:
    """把标签改动叠到 info 上（试运行不写盘时，用它模拟修复后的标签）。"""
    info = dict(info)
    for c in changes:
        field = _TAG_FIELDS.get(c.get("frame", ""))
        if field:
            info[field] = c["new"]
    return info


def _remove_empty_dirs_up_to(moved_from: str | Path, root: str | Path) -> int:
    """文件搬离源文件夹后，删除沿途变空的子目录（不删 root 本身）。

    从文件原所在目录向上试删，遇到非空目录或失败即停。返回删除的目录数。
    """
    removed = 0
    root_r = Path(root).resolve()
    d = Path(moved_from).parent.resolve()
    while d != root_r and root_r in d.parents:
        try:
            d.rmdir()
        except OSError:
            break
        removed += 1
        d = d.parent
    return removed


def _llm_ready(llm_config) -> bool:
    return llm_config is not None and llm_config.is_configured()


def _prebatch_verify(
    files: list[Path],
    repaired_infos: dict[str, dict],
    *,
    llm_config,
    llm_cache_dir: str | Path | None,
    progress=None,
) -> tuple[dict[str, dict], dict[str, dict], str]:
    """预批量大模型标签校对。

    repaired_infos[path] 为试运行编码修复后的内存标签；把文件名+修复后
    标签一次性发给大模型校对。返回 (verify_map, final_infos, error)：
    verify_map[path] 为校对结果（失败时为 {}，主循环不再重试）；
    final_infos[path] 为叠加校对结果后的内存标签（供归类计数用）；
    error 非空表示整批请求失败。
    progress(done, total, label)：每批请求前回调，label 为本批第一首歌。
    """
    from .llm_tagger import verify_tags
    from .encoding import fix_text

    items: list[dict] = []
    for p in files:
        info = repaired_infos[str(p)]
        # 文件名本身可能乱码，先修再送大模型
        display_name, _ = fix_text(p.name)
        items.append({"filename": display_name, **{
            k: (info.get(k) or "") for k in _VERIFY_FIELDS[1:]}})
    try:
        results = verify_tags(items, llm_config, cache_dir=llm_cache_dir,
                              progress=progress)
        error = ""
    except Exception as exc:  # noqa: BLE001 - 整批失败就整体回退
        results = [{} for _ in items]
        error = "%s: %s" % (type(exc).__name__, exc)
    verify_map: dict[str, dict] = {}
    final_infos: dict[str, dict] = {}
    for p, r in zip(files, results):
        verify_map[str(p)] = r
        info = dict(repaired_infos[str(p)])
        if r:
            for k in _VERIFY_FIELDS[1:]:
                v = (r.get(k) or "").strip()
                if v:
                    info[k] = v
        final_infos[str(p)] = info
    return verify_map, final_infos, error


def _prebatch_classify(
    files: list[Path],
    final_infos: dict[str, dict],
    learned: dict[str, str],
    *,
    llm_config,
    llm_cache_dir: str | Path | None,
    progress=None,
) -> str:
    """预批量归类分类：把需要问大模型的演唱者一次性查完，写进缓存。

    主循环里 classify() 逐文件调用时会命中缓存，不再发请求。
    progress(done, total, label)：每批请求前回调，label 为本批第一位演唱者。
    返回 error（空表示成功）。
    """
    from .llm_tagger import classify_artists

    need: list[tuple[str, str]] = []
    seen: set[str] = set()
    for p in files:
        info = final_infos.get(str(p))
        if info is None:
            info = read_info(p)
        artist = (info.get("artist") or "").strip()
        key = normalize_artist(artist)
        if not key or key in learned or key in seen:
            continue
        if guess_by_rules(artist, info.get("title") or "",
                          info.get("genre") or "") is not None:
            continue
        seen.add(key)
        need.append((artist, info.get("title") or ""))
    if not need:
        return ""
    try:
        classify_artists(need, llm_config, cache_dir=llm_cache_dir,
                         progress=progress)
        return ""
    except Exception as exc:  # noqa: BLE001 - 失败则主循环逐文件重试
        return "%s: %s" % (type(exc).__name__, exc)


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
    llm_verify: bool = False,
    template: str = TEMPLATE_PERSONAL,
    subdir_threshold: int = 3,
    merge_mode: bool = False,
    on_duplicate: str = "skip",
    stop_event=None,
    progress=None,
) -> dict:
    """执行完整流水线，返回 {"records", "total", "counts"}。

    progress: 可选回调 progress(index, total, phase, message)，
    phase 取值见 PHASE_NAMES 的键。
    template: personal（默认）或 classic；personal 下先学现有库再批量归类。
    subdir_threshold: personal 下同演唱者达到该数量才建子目录。
    llm_verify: True 且大模型可用时，标签修复阶段优先用大模型校对
    （文件名+现有标签 → 规范标签，批量一次查完）。
    stop_event: threading.Event（或任何有 is_set() 的对象），置起后在
    当前文件处理完后停下；已动过的文件进报告并标 stopped。
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

    verify_enabled = bool(llm_verify) and _llm_ready(llm_config)
    cache_opt = llm_cache_dir or cache_dir
    need_personal_counts = do_organize and template == TEMPLATE_PERSONAL

    # 预扫描：试运行编码修复（不写盘），得到修复后的内存标签。
    # 校对和归类计数都基于它，保证 Jay Chou/周杰伦这类计为同一人。
    repaired_infos: dict[str, dict] = {}
    if do_tags and (verify_enabled or need_personal_counts):
        for p in files:
            tag = fix_file(p, dry_run=True, guess_from_filename=False,
                           encodings=encodings, force_encoding=force_encoding)
            repaired_infos[str(p)] = _apply_tag_changes(
                read_info(p), tag["changes"])

    # 预批量 1：大模型标签校对（一次查完，主循环不再请求）
    verify_map: dict[str, dict] = {}
    final_infos: dict[str, dict] = repaired_infos
    if do_tags and verify_enabled:
        def _vp(done: int, total_b: int, label: str) -> None:
            _p(done, total_b, "llm_verify", label)
        verify_map, final_infos, err = _prebatch_verify(
            files, repaired_infos,
            llm_config=llm_config, llm_cache_dir=cache_opt,
            progress=_vp)
        if err:
            _p(0, total, "llm_verify", f"批量校对失败，已回退：{err}")

    learned: dict[str, str] = {}
    if do_organize and template == TEMPLATE_PERSONAL and library is not None:
        learned = learn_from_library(library)

    # 预批量 2：归类分类需要问大模型的演唱者，一次查完写进缓存
    if do_organize and template == TEMPLATE_PERSONAL and _llm_ready(llm_config):
        def _cp(done: int, total_b: int, label: str) -> None:
            _p(done, total_b, "llm_classify", label)
        err = _prebatch_classify(files, final_infos, learned,
                                 llm_config=llm_config,
                                 llm_cache_dir=cache_opt,
                                 progress=_cp)
        if err:
            _p(0, total, "llm_classify", f"批量分类失败，主循环逐文件重试：{err}")

    # 个人模板：预先统计每演唱者文件数（用校对/修复后的演唱者名）；
    # 达到阈值才建演唱者子目录
    counts: Counter = Counter()
    if need_personal_counts:
        for p in files:
            info = final_infos.get(str(p))
            if info is None:
                info = read_info(p)
            key = normalize_artist(info.get("artist"))
            if key:
                counts[key] += 1

    stopped = False

    def _cancelled() -> bool:
        return stop_event is not None and stop_event.is_set()

    records: list[dict] = []
    for i, p in enumerate(files, 1):
        if _cancelled():
            stopped = True
            break
        rec: dict = {"path": str(p), "tag_changes": [], "new_path": ""}

        # 1. 标签修复（含大模型优先校对）
        if do_tags:
            _p(i, total, "tags", str(p))
            tag = fix_file(
                p, dry_run=dry_run, backup_dir=backup_dir, backup_root=root,
                encodings=encodings, force_encoding=force_encoding,
                guess_from_filename=guess_from_filename,
                llm_config=llm_config, llm_cache_dir=cache_opt,
                llm_verify=llm_verify,
                llm_verify_result=verify_map.get(str(p)),
            )
            rec["tag_status"] = tag["status"]
            rec["tag_changes"] = tag["changes"]
            if tag.get("llm_error"):
                rec["llm_error"] = tag["llm_error"]
            if dry_run:
                info = _apply_tag_changes(read_info(p), tag["changes"])
            else:
                info = read_info(p)  # 用修复后的标签继续
        else:
            rec["tag_status"] = "skipped"
            info = read_info(p)

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
                _remove_empty_dirs_up_to(p, root)
            rec["organize_status"] = "skipped"
            rec["cover_status"] = "skipped"
            records.append(rec)
            continue

        target = p
        # 3. 归类整理
        if do_organize:
            _p(i, total, "organize", str(p))
            if template == TEMPLATE_PERSONAL:
                category, source, llm_err = classify(
                    info, learned, llm_config=llm_config,
                    llm_cache_dir=cache_opt)
                if llm_err:
                    prev = rec.get("llm_error")
                    rec["llm_error"] = f"{prev}; {llm_err}" if prev else llm_err
                rec["category_source"] = source
                key = normalize_artist(info.get("artist"))
                use_sub = use_artist_subdir(
                    library, category, info.get("artist") or "",
                    counts.get(key, 0), subdir_threshold)
                kwargs = {"template": template, "category": category,
                          "use_subdir": use_sub}
            else:
                kwargs = {"template": template}
            if merge_mode:
                org = merge_file(p, library, info, on_duplicate=on_duplicate,
                                 dry_run=dry_run, **kwargs)
            else:
                org = organize_file(p, library, info, dry_run=dry_run, **kwargs)
            rec["new_path"] = org["dest"]
            if org.get("category") == PENDING_CATEGORY:
                rec["new_path"] += "（待确认分类）"
            rec["organize_status"] = org["status"]
            rec["organize_action"] = org.get("action", org["status"])
            rec["category"] = org.get("category", "")
            if not dry_run and org["status"] == "moved":
                target = Path(org["dest"])
                _remove_empty_dirs_up_to(p, root)
        else:
            rec["organize_status"] = "skipped"

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
    if stopped:
        _p(total, total, "done", f"已停止：已处理 {len(records)}/{total} 个文件")
    else:
        _p(total, total, "done", f"处理完成：{len(records)} 个文件")

    summary: dict[str, int] = {}
    for r in records:
        for key in ("tag_status", "integrity_status", "cover_status"):
            base = str(r.get(key, "")).split("（")[0]  # 去掉备注括号，只按基础状态计数
            k = f"{key}={base}"
            summary[k] = summary.get(k, 0) + 1
    return {"records": records, "total": total, "counts": summary,
            "stopped": stopped}
