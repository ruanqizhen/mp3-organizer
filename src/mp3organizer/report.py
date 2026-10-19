"""运行报告：HTML + CSV。"""
from __future__ import annotations

import csv
import html
from datetime import datetime
from pathlib import Path

COLUMNS = ["path", "tag_status", "tag_changes", "integrity_status", "cover_status", "new_path"]
HEADERS = ["文件", "编码修复", "标签改动", "完整性", "封面", "新路径/备注"]


def _fmt_changes(changes: list[dict]) -> str:
    if not changes:
        return "—"
    return "<br>".join(
        f"{html.escape(c['frame'])}: {html.escape(c['old'])} → {html.escape(c['new'])}"
        for c in changes
    )


def build_html(records: list[dict], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    rows = []
    for r in records:
        rows.append(
            "<tr>"
            f"<td>{html.escape(r.get('path', ''))}</td>"
            f"<td>{html.escape(str(r.get('tag_status', '')))}</td>"
            f"<td>{_fmt_changes(r.get('tag_changes', []))}</td>"
            f"<td>{html.escape(str(r.get('integrity_status', '')))}</td>"
            f"<td>{html.escape(str(r.get('cover_status', '')))}</td>"
            f"<td>{html.escape(str(r.get('new_path', '')))}</td>"
            "</tr>"
        )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>MP3 整理报告</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;margin:24px;color:#333}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #ccc;padding:6px 8px;vertical-align:top;text-align:left}}
th{{background:#f2f2f2}}tr:nth-child(even){{background:#fafafa}}
.summary{{margin:12px 0;font-size:14px}}
</style></head>
<body>
<h2>MP3 整理报告</h2>
<p class="summary">生成时间：{now}　共 {len(records)} 个文件</p>
<table><tr>{"".join(f"<th>{h}</th>" for h in HEADERS)}</tr>
{"".join(rows)}
</table></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")
    return out_path


def build_csv(records: list[dict], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            row = dict(r)
            changes = row.get("tag_changes") or []
            row["tag_changes"] = "; ".join(
                f"{c['frame']}:{c['old']}->{c['new']}" for c in changes
            )
            writer.writerow(row)
    return out_path
