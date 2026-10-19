# mp3-organizer

Windows 老 MP3 批量整理工具。很多年前存的 MP3，标签乱码、文件夹混乱、有坏文件、没封面——这个工具一条龙处理：

1. **编码修复**：把误标为 latin1 的 GBK/Big5 等标签还原为 Unicode，统一写回 ID3v2.4 + UTF-8
2. **自动归类**：按 `类型 / 演唱者 / 演唱者 - 专辑` 整理到音乐库目录
3. **完整性检查**：mutagen 粗检 → mp3val 修复 → ffmpeg 终检；修不好的改名 `[损坏]` 隔离
4. **专辑封面**：MusicBrainz + Cover Art Archive 自动搜封面并嵌入（低分匹配人工确认，iTunes 回退）

设计文档见 [docs/设计方案.md](docs/设计方案.md)。

## 安装

```bat
pip install -r requirements.txt
```

需要完整性检查的话，另行下载 [mp3val](https://sourceforge.net/projects/mp3val/) 和
[ffmpeg](https://ffmpeg.org/download.html) 的 Windows 版，放到 PATH 里或用 `--mp3val` / `--ffmpeg` 指定路径。
没有它们也能跑，只是跳过对应级别的流检查。

## 图形界面（GUI）

```bat
python -m mp3organizer gui
REM 或安装后直接运行： mp3tool-gui
```

界面里选好源文件夹和音乐库，勾选要做的步骤，点"开始处理"。
默认是**试运行模式**（只生成报告不改文件），确认报告没问题后，
去掉"试运行"勾选再跑一次正式执行。处理在后台线程进行，
进度条和日志会实时更新；跑完点"打开 HTML 报告"看明细。

## 用法（命令行）

```bat
REM 先试运行，看报告，确认没问题再真正执行
python -m mp3organizer fix-tags D:\老音乐 --dry-run --report report.html

REM 完整流水线：修复编码 → 检查隔离 → 归类 → 找封面
python -m mp3organizer all D:\老音乐 D:\音乐库 ^
    --backup-dir D:\备份 --quarantine-dir D:\待处理 ^
    --report report.html --report-csv report.csv
```

分步命令：

```bat
python -m mp3organizer scan D:\老音乐                 # 扫描列出
python -m mp3organizer fix-tags D:\老音乐 --dry-run   # 只修标签
python -m mp3organizer fix-tags D:\老音乐 --guess-from-filename  # 无标签时从文件名建标签
python -m mp3organizer organize D:\老音乐 D:\音乐库   # 只归类
python -m mp3organizer check D:\老音乐 --quarantine-dir D:\待处理
python -m mp3organizer cover D:\音乐库 --min-score 0.75
```

`--force-encoding gbk` 可强制指定标签编码（跳过自动检测）；
`--encodings gbk,big5` 可调整自动尝试顺序（日韩收藏建议改顺序）。

打包成单文件 exe：`pip install pyinstaller` 后

```bat
REM 命令行版
pyinstaller --onefile --name mp3tool --paths src src/mp3organizer/cli.py
REM 图形界面版（--windowed 去掉控制台黑窗口）
pyinstaller --onefile --windowed --name MP3整理工具 --paths src src/mp3organizer/gui.py
```

mp3val.exe / ffmpeg.exe 如需随包分发，放到 exe 同目录即可（用 `--mp3val` /
`--ffmpeg` 参数或 PATH 指定位置）。

## 安全说明

- 任何改写/移动前都建议加 `--backup-dir`，原文件会自动备份，可回滚
- `--dry-run` 先跑一遍看报告，确认标签修复对照无误再正式执行
- 封面匹配分低于阈值的不会自动嵌入，会列在报告里人工确认

## 开发

```bat
pip install pytest
python -m pytest tests/ -q
```
