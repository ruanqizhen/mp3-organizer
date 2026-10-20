"""tkinter 图形界面：选文件夹、勾选项、点开始，后台线程跑流水线。"""
from __future__ import annotations

import os
import queue
import threading
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext
except ImportError:  # pragma: no cover
    raise SystemExit("需要 tkinter（Windows 官方 Python 自带；Linux 请安装 python3-tk）")

from .pipeline import run_pipeline, PHASE_NAMES
from .encoding import CANDIDATE_ENCODINGS


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MP3 整理工具")
        self.geometry("780x640")
        self._queue: queue.Queue = queue.Queue()
        self._running = False
        self._report_path: str | None = None
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self)
        frm.pack(fill="x", **pad)

        self._entries: dict[str, ttk.Entry] = {}
        for key, label in (("root", "MP3 源文件夹"),
                           ("library", "音乐库（归类目标）"),
                           ("backup", "备份目录（可选）"),
                           ("quarantine", "损坏隔离目录（可选）")):
            row = ttk.Frame(frm)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=label, width=20).pack(side="left")
            ent = ttk.Entry(row)
            ent.pack(side="left", fill="x", expand=True, padx=4)
            ttk.Button(row, text="浏览...",
                       command=lambda e=ent: self._browse(e)).pack(side="left")
            self._entries[key] = ent

        opts = ttk.LabelFrame(self, text="功能选项")
        opts.pack(fill="x", **pad)
        self._vars: dict[str, tk.BooleanVar] = {}
        for key, label, default in (
                ("do_tags", "修复标签编码为 Unicode", True),
                ("guess", "无标签时从文件名猜标签", False),
                ("do_check", "完整性检查（需 mp3val/ffmpeg，无则跳过）", True),
                ("do_organize", "按类型/演唱者归类整理", True),
                ("do_cover", "自动下载专辑封面并嵌入", True),
                ("dry_run", "试运行（只生成报告，不修改任何文件）", True)):
            var = tk.BooleanVar(value=default)
            ttk.Checkbutton(opts, text=label, variable=var).pack(anchor="w", padx=8, pady=1)
            self._vars[key] = var

        prow = ttk.Frame(opts)
        prow.pack(fill="x", padx=8, pady=4)
        ttk.Label(prow, text="封面匹配分阈值").pack(side="left")
        self._min_score = tk.StringVar(value="0.75")
        ttk.Entry(prow, textvariable=self._min_score, width=6).pack(side="left", padx=4)
        ttk.Label(prow, text="编码尝试顺序").pack(side="left", padx=(12, 0))
        self._encodings = tk.StringVar(value=",".join(CANDIDATE_ENCODINGS))
        ttk.Entry(prow, textvariable=self._encodings, width=30).pack(side="left", padx=4)

        self._build_llm_section(pad)

        runfrm = ttk.Frame(self)
        runfrm.pack(fill="x", **pad)
        self._start_btn = ttk.Button(runfrm, text="开始处理", command=self._start)
        self._start_btn.pack(side="left")
        self._report_btn = ttk.Button(runfrm, text="打开 HTML 报告",
                                      command=self._open_report, state="disabled")
        self._report_btn.pack(side="left", padx=8)
        self._status = ttk.Label(runfrm, text="就绪")
        self._status.pack(side="left", padx=8)
        self._bar = ttk.Progressbar(self, mode="determinate", maximum=100)
        self._bar.pack(fill="x", **pad)

        self._log = scrolledtext.ScrolledText(self, height=16, state="disabled")
        self._log.pack(fill="both", expand=True, **pad)
        self._load_llm_settings()

    def _build_llm_section(self, pad: dict) -> None:
        """大模型猜标签设置区（OpenAI 兼容接口，用户自己配模型）。"""
        frm = ttk.LabelFrame(self, text="大模型猜标签（可选）")
        frm.pack(fill="x", **pad)
        self._llm_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="启用：文件名用规则猜不出时，调用大模型",
                        variable=self._llm_enabled).pack(anchor="w", padx=8, pady=2)
        self._llm_fields: dict[str, tk.StringVar] = {}
        for key, label, default in (
                ("base_url", "API 地址", "https://api.deepseek.com"),
                ("api_key", "API Key", ""),
                ("model", "模型名", "deepseek-chat")):
            row = ttk.Frame(frm)
            row.pack(fill="x", padx=8, pady=1)
            ttk.Label(row, text=label, width=10).pack(side="left")
            var = tk.StringVar(value=default)
            ent = ttk.Entry(row, textvariable=var,
                            show="*" if key == "api_key" else "")
            ent.pack(side="left", fill="x", expand=True)
            self._llm_fields[key] = var
        ttk.Label(frm, text="Key 建议用环境变量 MP3ORGANIZER_LLM_API_KEY；"
                            "本机 Ollama 填 http://localhost:11434/v1，Key 可空",
                  foreground="gray").pack(anchor="w", padx=8, pady=2)

    @staticmethod
    def _llm_config_file() -> Path:
        return Path.home() / ".config" / "mp3-organizer" / "llm.json"

    def _load_llm_settings(self) -> None:
        try:
            import json
            data = json.loads(self._llm_config_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for key, var in self._llm_fields.items():
            if data.get(key):
                var.set(data[key])
        self._llm_enabled.set(bool(data.get("enabled")))

    def _save_llm_settings(self) -> None:
        import json
        data = {"enabled": self._llm_enabled.get()}
        data.update({k: v.get().strip() for k, v in self._llm_fields.items()})
        p = self._llm_config_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def _llm_config_or_error(self):
        """返回 (LLMConfig|None, 错误信息|None)。"""
        if not self._llm_enabled.get():
            return None, None
        from .llm_tagger import LLMConfig
        cfg = LLMConfig(
            base_url=self._llm_fields["base_url"].get().strip()
            or "https://api.deepseek.com",
            api_key=self._llm_fields["api_key"].get().strip()
            or os.environ.get("MP3ORGANIZER_LLM_API_KEY", ""),
            model=self._llm_fields["model"].get().strip() or "deepseek-chat",
        )
        if not cfg.is_configured():
            return None, "请填写 API 地址和模型名"
        if cfg.needs_key():
            return None, "请填写 API Key（或设置环境变量 MP3ORGANIZER_LLM_API_KEY）"
        return cfg, None

    def _browse(self, entry: ttk.Entry) -> None:
        d = filedialog.askdirectory()
        if d:
            entry.delete(0, "end")
            entry.insert(0, d)

    def _log_line(self, text: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", text + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _start(self) -> None:
        if self._running:
            return
        root = self._entries["root"].get().strip()
        library = self._entries["library"].get().strip()
        if not root or not Path(root).is_dir():
            messagebox.showerror("错误", "请选择有效的 MP3 源文件夹")
            return
        if self._vars["do_organize"].get() and not library:
            messagebox.showerror("错误", "启用归类整理时必须指定音乐库目录")
            return
        try:
            min_score = float(self._min_score.get())
        except ValueError:
            messagebox.showerror("错误", "匹配分阈值必须是数字")
            return
        encodings = tuple(e.strip() for e in self._encodings.get().split(",") if e.strip())
        if not encodings:
            messagebox.showerror("错误", "编码尝试顺序不能为空")
            return
        llm_config, llm_err = self._llm_config_or_error()
        if llm_err:
            messagebox.showerror("错误", llm_err)
            return
        self._save_llm_settings()

        self._running = True
        self._start_btn.configure(state="disabled")
        self._report_btn.configure(state="disabled")
        self._report_path = str(Path(root) / "mp3整理报告.html")
        self._log_line("=" * 40)
        self._log_line("开始处理：" + root)
        if self._vars["dry_run"].get():
            self._log_line("【试运行模式】不会修改任何文件，只生成报告")
        if llm_config is not None:
            self._log_line("大模型猜标签已启用（%s）" % llm_config.model)

        args = dict(
            dry_run=self._vars["dry_run"].get(),
            backup_dir=self._entries["backup"].get().strip() or None,
            quarantine_dir=self._entries["quarantine"].get().strip() or None,
            do_tags=self._vars["do_tags"].get(),
            do_check=self._vars["do_check"].get(),
            do_organize=self._vars["do_organize"].get(),
            do_cover=self._vars["do_cover"].get(),
            encodings=encodings,
            guess_from_filename=self._vars["guess"].get(),
            llm_config=llm_config,
            min_score=min_score,
            report_html=self._report_path,
        )
        threading.Thread(target=self._worker,
                         args=(root, library or None, args),
                         daemon=True).start()
        self.after(120, self._poll)

    def _worker(self, root: str, library: str | None, args: dict) -> None:
        try:
            result = run_pipeline(
                root, library,
                progress=lambda i, t, ph, msg: self._queue.put(("progress", i, t, ph, msg)),
                **args,
            )
            self._queue.put(("done", result))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("error", "%s: %s" % (type(exc).__name__, exc)))

    def _poll(self) -> None:
        try:
            while True:
                msg = self._queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, i, total, phase, text = msg
                    pct = (i / total * 100) if total else 0
                    self._bar["value"] = pct
                    name = PHASE_NAMES.get(phase, phase)
                    self._status.configure(text="%s %d/%d" % (name, i, total))
                elif kind == "done":
                    self._finish(msg[1])
                    return
                elif kind == "error":
                    self._log_line("出错：" + msg[1])
                    self._finish(None)
                    return
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _finish(self, result: dict | None) -> None:
        self._running = False
        self._start_btn.configure(state="normal")
        self._bar["value"] = 100 if result else 0
        if result is None:
            self._status.configure(text="出错，已停止")
            return
        self._status.configure(text="完成，共 %d 个文件" % result["total"])
        self._log_line("-" * 40)
        for k, v in sorted(result["counts"].items()):
            self._log_line("%s: %d" % (k, v))
        shown = 0
        for r in result["records"]:
            interesting = (
                r.get("tag_status") in ("fixed", "guessed")
                or r.get("integrity_status") in ("fixed", "broken", "unreadable")
                or str(r.get("cover_status", "")).startswith("needs_review")
                or r.get("llm_error")
            )
            if interesting and shown < 100:
                line = "[%s/%s/%s] %s" % (
                    r.get("tag_status"), r.get("integrity_status"),
                    r.get("cover_status"), r["path"])
                if r.get("llm_error"):
                    line += "（大模型调用失败，已回退：%s）" % r["llm_error"]
                self._log_line(line)
                shown += 1
        if shown >= 100:
            self._log_line("（仅显示前 100 条，完整清单见 HTML 报告）")
        self._log_line("报告已生成：" + str(self._report_path))
        if self._report_path and Path(self._report_path).exists():
            self._report_btn.configure(state="normal")

    def _open_report(self) -> None:
        if not self._report_path:
            return
        try:
            if os.name == "nt":
                os.startfile(self._report_path)  # noqa: S606
            else:
                webbrowser.open("file://" + self._report_path)
        except OSError as exc:
            messagebox.showerror("错误", "无法打开报告：%s" % exc)


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
