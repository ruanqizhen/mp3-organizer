# mp3-organizer

Windows 老 MP3 批量整理工具。很多年前存的 MP3，标签乱码、文件夹混乱、有坏文件、没封面——这个工具一条龙处理：

1. **编码修复**：把误标为 latin1 的 GBK/Big5 等标签还原为 Unicode，统一写回 ID3v2.4 + UTF-8
2. **自动归类**：按 `语言.性别 / 演唱者` 整理（从现有音乐库学习分类，`--template classic` 切回经典方式）
3. **完整性检查**：mutagen 粗检 → mp3val 修复 → ffmpeg 终检；修不好的改名 `[损坏]` 隔离
4. **专辑封面**：MusicBrainz + Cover Art Archive 自动搜封面并嵌入（低分匹配人工确认，iTunes 回退）

设计文档见 [docs/设计方案.md](docs/设计方案.md)。

## 安装

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

之后每次新开终端，先执行 `.venv\Scripts\activate`，再运行 `mp3tool ...` 或 `mp3tool-gui`。

需要完整性检查的话，另行下载 [mp3val](https://sourceforge.net/projects/mp3val/) 和
[ffmpeg](https://ffmpeg.org/download.html) 的 Windows 版，放到 PATH 里或用 `--mp3val` / `--ffmpeg` 指定路径。
没有它们也能跑，只是跳过对应级别的流检查。

## 图形界面（GUI）

```bat
.venv\Scripts\activate
mp3tool-gui
```

可编辑安装后 `mp3tool` / `mp3tool-gui` 直接可用，无需设置 PYTHONPATH（注意：CMD 的 `set` 语法在 PowerShell 里不生效，不要用来设环境变量）。

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
python -m mp3organizer organize D:\老音乐 D:\音乐库   # 只归类（学习音乐库现有分类）
python -m mp3organizer organize D:\老音乐 D:\音乐库 --template classic  # 经典 类型/演唱者 方式
python -m mp3organizer check D:\老音乐 --quarantine-dir D:\待处理
python -m mp3organizer cover D:\音乐库 --min-score 0.75
```

`--force-encoding gbk` 可强制指定标签编码（跳过自动检测）；
`--encodings gbk,big5` 可调整自动尝试顺序（日韩收藏建议改顺序）。

## 大模型猜标签（可选）

文件名太乱、正则猜不出时（拼音缩写、错别字、无分隔符等），可以回退到大模型。
大模型凭中文流行音乐知识推断，还能顺带补全专辑、年份、类型：

```bat
REM 先配好 Key（建议用环境变量，避免留在命令行历史里）
set MP3ORGANIZER_LLM_API_KEY=你的key

python -m mp3organizer fix-tags D:\老音乐 --guess-from-filename --llm
```

模型完全由你自己设置（OpenAI 兼容接口）：

| 参数 | 说明 | 默认值 |
|---|---|---|
| `--llm-base-url` | API 地址 | `https://api.deepseek.com`（环境变量 `MP3ORGANIZER_LLM_BASE_URL`） |
| `--llm-api-key` | API Key | 环境变量 `MP3ORGANIZER_LLM_API_KEY` / `DEEPSEEK_API_KEY` |
| `--llm-model` | 模型名 | `deepseek-chat`（环境变量 `MP3ORGANIZER_LLM_MODEL`） |

常见配置：

- **DeepSeek**：地址默认就是，填 Key 和 `deepseek-chat` 即可（便宜，中文好）
- **Gemini**：地址 `https://generativelanguage.googleapis.com/v1beta/openai/`，模型 `gemini-2.0-flash`
- **本地 Ollama**：地址 `http://localhost:11434/v1`，模型如 `qwen2.5:7b`，Key 可空（离线可用）

图形界面里也有对应的设置区，填一次会自动记住（`~/.config/mp3-organizer/llm.json`）。
规则能猜出的文件名不会调大模型；调过的文件名会本地缓存，不重复花钱；
大模型猜出的项在报告里标"大模型猜测·置信度"，失败会自动回退到正则。

## 整理杂乱文件名（rename）

有些文件名特别长、夹杂各种噪音（码率标记、网址、"免费下载"、括号备注等），
用大模型找出真正的演唱者和曲名，改成标准文件名（默认"演唱者 - 标题.mp3"），
并顺手把解析出的演唱者/标题填入空的标签字段：

```bat
set MP3ORGANIZER_LLM_API_KEY=你的key

REM 先试运行看效果
python -m mp3organizer rename D:\老音乐 --llm --dry-run --report 报告.html

REM 确认无误后正式执行
python -m mp3organizer rename D:\老音乐 --llm
```

- 不加 `--llm` 也能用：正则处理简单情况（如 `王菲-红豆.mp3` → `王菲 - 红豆.mp3`），免费
- 已经是标准格式的文件自动跳过，不调大模型
- 同名冲突自动加 `(1)` 后缀；`--format "{title} - {artist}"` 可自定义模板
- `--no-tag-write` 只改文件名、不写标签

## 合并到已有音乐库（merge）

一个文件夹是已经整理好的音乐库，另一个文件夹是没整理的散文件：批量整理散文件
（修标签→查完整性→归类→封面），去重合并进音乐库：

```bat
python -m mp3organizer merge D:\未整理 D:\音乐库 --dry-run
python -m mp3organizer merge D:\未整理 D:\音乐库 --guess-from-filename --llm
```

- 判重规则：目标位置已存在**文件大小相同且时长相差 2 秒内**的文件 → 视为重复，跳过
- `--on-duplicate`：`skip` 跳过重复（默认）；`rename` 改名并入；`replace` 覆盖
- 图形界面里勾选"合并模式：音乐库已有同曲目时跳过（去重并入）"效果相同
- 归类模板默认 personal：`音乐库/语言.性别/[演唱者/]演唱者 - 标题.mp3`，从音乐库现有结构自动学习"演唱者→分类"；`--template classic` 切回经典方式
- `--subdir-threshold N`（默认 3）：同演唱者达到 N 首才建子目录，否则放分类顶层；库里已有该演唱者目录时直接并入
- 定不了分类的进"待分类"并在报告里标注，人工拖到正确位置后下次自动记住；加 `--llm` 可让大模型定语言和性别

推荐流程：先 `rename` 把文件名理顺，再 `merge` 并入音乐库。

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
