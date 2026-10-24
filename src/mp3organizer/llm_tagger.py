"""大模型猜标签：OpenAI 兼容接口，模型由用户自己配置。

支持 DeepSeek / Gemini(OpenAI 兼容模式) / OpenAI / 本地 Ollama(v1 接口)等。
把文件名批量发给大模型，返回最可能的标题/演唱者/专辑/类型/年份。

作为 filename_tags 正则猜测的回退：规则猜不出时才调用，又省又准。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import requests

from .categories import normalize_artist


class LLMError(RuntimeError):
    """调用大模型失败。"""


@dataclass
class LLMConfig:
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-chat"
    # 每批歌曲数：verify 类重 prompt 下 10 最合适——输出约 3000 tokens，
    # 留在 max_tokens=4000 内不被截断；输入约 5000 tokens，8K 上下文模型也从容；
    # 整批失败时只浪费 10 个（失败无部分重试）。
    batch_size: int = 10
    timeout: int = 60
    # 是否用本地缓存。False 时每次都重新调用大模型，不读也不写缓存。
    use_cache: bool = True

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            base_url=os.environ.get(
                "MP3ORGANIZER_LLM_BASE_URL", "https://api.deepseek.com"),
            api_key=os.environ.get("MP3ORGANIZER_LLM_API_KEY", "")
            or os.environ.get("DEEPSEEK_API_KEY", ""),
            model=os.environ.get("MP3ORGANIZER_LLM_MODEL", "deepseek-chat"),
        )

    def is_configured(self) -> bool:
        return bool(self.base_url.strip() and self.model.strip())

    def needs_key(self) -> bool:
        """非本机地址又没填 Key 时，需要提醒用户补 Key。"""
        if self.api_key.strip():
            return False
        host = self.base_url.lower()
        return "localhost" not in host and "127.0.0.1" not in host


SYSTEM_PROMPT = """你是中文老歌收藏整理助手。用户会给你一批 MP3 文件名（JSON 数组），请推断每首歌最可能的元数据。

要求：
1. 只返回 JSON 数组，与输入顺序一一对应，不要输出其他任何内容。
2. 每项包含字段：title（歌名）、artist（演唱者/演奏者）、album（专辑，未知填空字符串）、genre（流派，如 流行/摇滚/民谣/古典/爵士/电子/说唱/乡村/轻音乐）、year（发行年份，未知填空字符串）、confidence（0~1，你对这次推断的把握）。
3. 所有中文输出一律用简体中文。
4. 发挥你的中文流行音乐知识：拼音缩写、错别字、顺序颠倒都要尽力还原。
5. 实在看不出的文件名：title 填去掉后缀的文件名，artist/album/genre/year 填空字符串，confidence 填 0.1，不要编造演唱者。

示例：
输入：["周杰伦 - 夜曲.mp3", "yq_zjl.mp3", "未知歌曲.mp3"]
输出：[{"title": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦", "genre": "流行", "year": "2005", "confidence": 0.95}, {"title": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦", "genre": "流行", "year": "2005", "confidence": 0.7}, {"title": "未知歌曲", "artist": "", "album": "", "genre": "", "year": "", "confidence": 0.1}]
"""


def _cache_path(cache_dir: str | Path | None, cache_name: str) -> Path:
    if cache_dir is None:
        cache_dir = Path.home() / ".cache" / "mp3-organizer"
    return Path(cache_dir) / cache_name


# 当次运行内的内存缓存：use_cache=False 时用它做去重（同一艺人/同一项
# 在一次运行里只查一次），不落盘。run_pipeline 开始时清空，保证"每一次
# 运行"都是干净的（GUI 常驻进程多次点开始也不串）。
_session_cache: dict[tuple[str, str], dict] = {}


def clear_session_cache() -> None:
    """清空当次运行的内存缓存。"""
    _session_cache.clear()


def _load_cache(cache_dir: str | Path | None, cache_name: str) -> dict:
    try:
        return json.loads(_cache_path(cache_dir, cache_name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache_dir: str | Path | None, cache_name: str, cache: dict) -> None:
    p = _cache_path(cache_dir, cache_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def _extract_json_array(text: str) -> list:
    """从模型输出中提取 JSON 数组；提不出返回空列表。"""
    text = text.strip()
    if text.startswith("```"):
        body = text.strip("`").strip()
        lines = body.split("\n", 1)
        if len(lines) == 2 and lines[0].strip().isalpha():
            body = lines[1]
        text = body
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except ValueError:
        pass
    start, end = text.find("["), text.rfind("]")
    if 0 <= start < end:
        try:
            data = json.loads(text[start:end + 1])
            return data if isinstance(data, list) else []
        except ValueError:
            return []
    return []


def _query_batch(
    filenames: list[str], config: LLMConfig, system_prompt: str, request_fn=None
) -> list[dict]:
    url = config.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.api_key.strip():
        headers["Authorization"] = "Bearer " + config.api_key.strip()
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(filenames, ensure_ascii=False)},
        ],
        "temperature": 0,
        "max_tokens": 4000,
    }
    try:
        if request_fn is not None:
            data = request_fn(url, headers, payload, config.timeout)
        else:
            resp = requests.post(url, headers=headers, json=payload,
                                 timeout=config.timeout)
            resp.raise_for_status()
            data = resp.json()
    except requests.RequestException as exc:
        raise LLMError("请求大模型失败：%s" % exc) from exc
    except ValueError as exc:
        raise LLMError("大模型返回的不是 JSON：%s" % exc) from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("大模型返回格式异常：%s" % exc) from exc
    items = _extract_json_array(content)
    if len(items) != len(filenames) or not all(isinstance(x, dict) for x in items):
        return [{} for _ in filenames]
    return items


def _query_cached(
    filenames: list[str],
    config: LLMConfig,
    system_prompt: str,
    cache_name: str,
    *,
    cache_dir: str | Path | None = None,
    request_fn=None,
    progress=None,
    label_fn=None,
    stats: dict | None = None,
) -> list[dict]:
    """批量查询，带本地缓存。返回与 filenames 一一对应的结果 dict 列表。

    request_fn(url, headers, payload, timeout) -> 响应 JSON（dict），供测试注入。
    progress(done, total, label)：每批请求前回调一次，label 为本批第一项的
    显示名（label_fn(key) 算出；不传则直接用 key）。
    stats：可选 dict，填入 {"total": 总数, "cached": 缓存命中数,
    "queried": 实际请求数}，供调用方显示。
    失败时抛 LLMError；调用前请确认 config.is_configured()。
    """
    if not config.is_configured():
        raise LLMError("大模型未配置：需要 API 地址和模型名")
    # use_cache=False：不用以前存的持久缓存，但当次运行内的结果
    # 进内存会话缓存去重（不落盘）
    if config.use_cache:
        cache = _load_cache(cache_dir, cache_name)
        todo = [f for f in filenames if f not in cache]
    else:
        cache = {}
        todo = []
        for f in filenames:
            hit = _session_cache.get((cache_name, f))
            if hit is not None:
                cache[f] = hit
            else:
                todo.append(f)
    if stats is not None:
        stats.update(total=len(filenames),
                     cached=len(filenames) - len(todo),
                     queried=len(todo))
    total = len(todo)
    bs = max(1, config.batch_size)
    for i in range(0, len(todo), bs):
        batch = todo[i:i + bs]
        if progress is not None:
            key0 = batch[0]
            progress(i, total, label_fn(key0) if label_fn else key0)
        results = _query_batch(batch, config, system_prompt, request_fn)
        for name, item in zip(batch, results):
            cache[name] = item
            if not config.use_cache:
                _session_cache[(cache_name, name)] = item
        if config.use_cache:
            _save_cache(cache_dir, cache_name, cache)
    return [cache.get(f, {}) for f in filenames]


def query_llm(
    filenames: list[str],
    config: LLMConfig,
    *,
    cache_dir: str | Path | None = None,
    request_fn=None,
    progress=None,
) -> list[dict]:
    """猜标签：文件名 → 标题/演唱者/专辑/类型/年份/置信度。

    progress(done, total, label)：每批请求前回调，label 为本批第一个文件名。
    """
    return _query_cached(filenames, config, SYSTEM_PROMPT, "llm_tags.json",
                         cache_dir=cache_dir, request_fn=request_fn,
                         progress=progress)


RENAME_SYSTEM_PROMPT = """你是文件名整理助手。用户会给你一批 MP3 文件名（JSON 数组），每个都很长很乱，夹杂着各种无关信息。
请找出每首歌真正的"演唱者"和"曲名"，忽略码率标记（如 320K/FLAC）、网址、"免费下载"、括号里的备注、开头序号、榜单/系列前缀（如 Classic Oldies 666）等噪音。

要求：
1. 只返回 JSON 数组，与输入顺序一一对应，不要输出其他任何内容。
2. 每项包含字段：artist（演唱者/演奏者，多人用", "连接，如"张学友, 汤宝如"，未知填空字符串）、title（曲名）、confidence（0~1，你对这次解析的把握）。
3. 发挥你的中文流行音乐知识：拼音缩写、错别字、顺序颠倒都要尽力还原。
4. 实在看不出的文件名：title 填去掉噪音后最像曲名的部分，artist 填空字符串，confidence 填 0.1，不要编造演唱者。
5. 所有中文输出一律用简体中文。

示例：
输入：["[320K]周杰伦-夜曲(官方正式版)_www.xiami.com免费下载.mp3", "03. 王菲 - 红豆 (Live现场版).mp3", "Classic Oldies 666 - 乌兰巴托的夜 洋澜一.mp3"]
输出：[{"artist": "周杰伦", "title": "夜曲", "confidence": 0.95}, {"artist": "王菲", "title": "红豆", "confidence": 0.9}, {"artist": "洋澜一", "title": "乌兰巴托的夜", "confidence": 0.85}]
"""


def parse_filenames(
    filenames: list[str],
    config: LLMConfig,
    *,
    cache_dir: str | Path | None = None,
    request_fn=None,
    progress=None,
) -> list[dict]:
    """解析杂乱文件名 → [{artist, title, confidence}]，用于整理标准文件名。

    progress(done, total, label)：每批请求前回调，label 为本批第一个文件名。
    """
    return _query_cached(filenames, config, RENAME_SYSTEM_PROMPT, "llm_rename.json",
                         cache_dir=cache_dir, request_fn=request_fn,
                         progress=progress)


CLASSIFY_SYSTEM_PROMPT = """你是中文音乐收藏整理助手。用户会给你一批演唱者（JSON 数组，每项 {"artist": 演唱者, "title": 代表作}），请判断每位演唱者属于哪个分类。

分类只能是以下 7 个之一：
- 华语.男：演唱中文歌的男歌手
- 华语.女：演唱中文歌的女歌手
- 华语.组合：三人及以上的华语乐队/组合
- 华语.双人：华语双人对唱（演唱者栏是两个人）
- 英语：演唱英文歌的歌手/组合
- 纯音乐：演奏纯音乐（无演唱）的音乐人/乐团
- 其它语言：日韩、俄语、法语等非中英语言的歌手

要求：
1. 只返回 JSON 数组，与输入顺序一一对应，不要输出其他任何内容。
2. 每项包含字段：category（必须是以上 7 个之一）、confidence（0~1）。
3. 以演唱者母语/主要作品语言判断；拿不准时选最可能的一项并降低 confidence。

示例：
输入：[{"artist": "周杰伦", "title": "夜曲"}, {"artist": "Adele", "title": "Hello"}]
输出：[{"category": "华语.男", "confidence": 0.99}, {"category": "英语", "confidence": 0.99}]
"""


def classify_artists(
    pairs: list[tuple[str, str]],
    config: LLMConfig,
    *,
    cache_dir: str | Path | None = None,
    request_fn=None,
    progress=None,
    stats: dict | None = None,
) -> list[dict]:
    """演唱者 → 语言.性别分类。pairs 为 [(artist, title)]，按演唱者缓存。

    progress(done, total, label)：每批请求前回调，label 为本批第一位演唱者。
    """
    if not config.is_configured():
        raise LLMError("大模型未配置：需要 API 地址和模型名")
    keys = [normalize_artist(a) or ("title:" + t.strip().casefold())
            for a, t in pairs]
    # use_cache=False：不用以前存的持久缓存，但当次运行内的结果
    # 进内存会话缓存去重（不落盘）
    if config.use_cache:
        cache = _load_cache(cache_dir, "llm_classify.json")
    else:
        cache = {}
        for k in set(keys):
            hit = _session_cache.get(("llm_classify.json", k))
            if hit is not None:
                cache[k] = hit
    seen = set(cache)
    todo = []
    for i, k in enumerate(keys):
        if k not in seen:  # 同一批里同一演唱者只问一次
            seen.add(k)
            todo.append(i)
    total = len(todo)
    if stats is not None:
        stats.update(total=len(pairs), cached=len(pairs) - total,
                     queried=total)
    bs = max(1, config.batch_size)
    for s in range(0, len(todo), bs):
        batch_idx = todo[s:s + bs]
        if progress is not None:
            a0, t0 = pairs[batch_idx[0]]
            progress(s, total, a0 or t0)
        batch = [{"artist": pairs[i][0], "title": pairs[i][1]}
                 for i in batch_idx]
        results = _query_batch(batch, config, CLASSIFY_SYSTEM_PROMPT, request_fn)
        for i, item in zip(batch_idx, results):
            cache[keys[i]] = item
            if not config.use_cache:
                _session_cache[("llm_classify.json", keys[i])] = item
        if config.use_cache:
            _save_cache(cache_dir, "llm_classify.json", cache)
    return [cache.get(k, {}) for k in keys]


VERIFY_SYSTEM_PROMPT = """你是中文音乐标签校对助手。用户会给你一批 MP3 文件的信息（JSON 数组），每项包含 path（相对路径，含目录和文件名；目录名可作演唱者/分类参考）、filename（文件名，含后缀）、title、artist、album、genre、year、track（当前标签值，可能为空、互相矛盾，或把多种信息挤在一个字段里）。

任务：综合文件名和现有标签，为每首歌生成正确的标签。

要求：
1. 只返回 JSON 数组，与输入顺序一一对应，不要输出其他任何内容。
2. 每项包含字段：title（纯歌名，不含演唱者/专辑/Feat/年份等杂质）、artist（演唱者标准名，多人用", "连接，如"张学友, 汤宝如"）、album（专辑，未知填空字符串）、genre（流派，如 流行/摇滚/民谣/古典/爵士/电子/说唱/乡村/轻音乐，未知填空字符串）、year（4 位年份，未知填空字符串）、track（音轨号，未知填空字符串）、confidence（0~1）、note（中文，一句话说明你改了什么；没改填空字符串）。
3. 演唱者统一用其最广为人知的中文简体全名：Jay Chou→周杰伦，Namewee→黄明志，GEM/GEMblog/GEM鄧紫棋/鄧紫棋→邓紫棋，JJ Lin→林俊杰；拼写变体/昵称/账号名一律归并；中英文混写时以中文名为准；"周杰伦 Jay Chou"这类视为同一人；下划线连接的多个名字拆成多人（王宇宙Leto_喬浚丞→"王宇宙Leto, 乔浚丞"）。
4. 标题里常混入的信息要拆出来：【】内的歌名/专辑名、Ft./feat./Featuring/合唱后面的是合作艺人（并入 artist）、末尾 —/–/by + 人名这类演唱者后缀（拆出并入 artist）、4 位年份、专辑名、"亚洲通吃"这类版本/企划名归入 album；括号里是副标题/备选名时取主标题（"若月亮沒來 (若是月亮還沒來)"取"若月亮沒來"）。
5. 所有中文输出一律用简体中文（title/artist/album/genre 一律简体）。
6. 去掉噪音：PLAY MUSIC 这类来源前缀、www.xxx.com 这类站点前缀、【动态歌词/lyric video】这类标记、with English/中英字幕这类标记、『』「」里的歌词引用、官方MV/OST 标记；演唱者栏是频道名/上传者名（如 XXX Music Channel、YOYOROCK 滾石移動）时视为噪音，从标题里找真正演唱者。
7. 《》书名号里中英混写时取中文（《不染 Unsullied》取"不染"）；【香蜜沉沉燼如霜主題曲】这类主题曲信息可归入 album。
8. 拼音缩写、错别字、语序颠倒尽力还原（如 "yq_zjl"→夜曲/周杰伦）。
9. 不要编造：实在判断不出，title 用去掉噪音后的文件名，artist/album/genre/year/track 填空字符串，confidence 不超过 0.3。

示例：
输入：[{"path": "待整理/Namewee-胡志明的雨.mp3", "filename": "Namewee 黃明志【胡志明的雨】Ft. 越南小天王胡光孝 亞洲通吃 2017 All Eat Asia.mp3", "title": "Namewee 黃明志【胡志明的雨】Ft. 越南小天王胡光孝 亞洲通吃 2017 All Eat Asia", "artist": "Namewee", "album": "", "genre": "", "year": "", "track": ""}]
输出：[{"title": "胡志明的雨", "artist": "黄明志, 胡光孝", "album": "亚洲通吃", "genre": "流行", "year": "2017", "track": "", "confidence": 0.9, "note": "标题拆出歌名/合作艺人/专辑/年份；Namewee 统一为黄明志"}]

示例 2（标题把演唱者/歌名/噪音全塞在一起，演唱者栏为空）：
输入：[{"path": "待整理/PLAY MUSIC - 篇章.mp3", "filename": "PLAY MUSIC - 篇章 Pages - 张韶涵 _ 王赫野 Angela Chang _ Wang HeYe【动态歌词 lyric video】with English 中英.mp3", "title": "PLAY MUSIC - 篇章 Pages - 张韶涵 _ 王赫野 Angela Chang _ Wang HeYe【动态歌词 lyric video】with English 中英", "artist": "", "album": "", "genre": "", "year": "", "track": ""}]
输出：[{"title": "篇章", "artist": "张韶涵, 王赫野", "album": "", "genre": "流行", "year": "", "track": "", "confidence": 0.85, "note": "标题含演唱者/歌名/噪音：拆出歌名篇章与演唱者；中英重复保留中文；去掉 PLAY MUSIC 前缀与歌词视频噪音"}]

示例 3（演唱者栏是频道名，标题塞了备选标题/演唱者/歌词引用）：
输入：[{"path": "待整理/SKY Music Channel - 若月亮沒來.mp3", "filename": "SKY Music Channel - 若月亮沒來 (若是月亮還沒來) - 王宇宙Leto_喬浚丞『若是晨風還沒來，晚風也可吹入懷』【動態歌詞】.mp3", "title": "若月亮沒來 (若是月亮還沒來) - 王宇宙Leto_喬浚丞『若是晨風還沒來，晚風也可吹入懷』【動態歌詞】", "artist": "SKY Music Channel", "album": "", "genre": "", "year": "", "track": ""}]
输出：[{"title": "若月亮沒來", "artist": "王宇宙Leto, 乔浚丞", "album": "", "genre": "流行", "year": "", "track": "", "confidence": 0.8, "note": "演唱者栏是频道名视为噪音，从标题找出真正演唱者；下划线拆成两人；括号备选标题取主标题；去掉歌词引用与动态歌词噪音"}]

示例 4（频道前缀 + 中英艺人 + 书名号标题 + 主题曲/OST 噪音）：
输入：[{"path": "待整理/YOYOROCK - 不染.mp3", "filename": "YOYOROCK 滾石移動 - 毛不易 Mao Buyi《不染 Unsullied》【香蜜沉沉燼如霜主題曲】官方MV Ashes of Love OST.mp3", "title": "", "artist": "", "album": "", "genre": "", "year": "", "track": ""}]
输出：[{"title": "不染", "artist": "毛不易", "album": "香蜜沉沉烬如霜", "genre": "流行", "year": "", "track": "", "confidence": 0.85, "note": "去掉频道前缀；中英重复保留中文；书名号取中文歌名；主题曲信息归入专辑；去掉官方MV/OST噪音"}]

示例 5（站点前缀 + 音轨号；文件名乱码已在送模型前修好）：
输入：[{"path": "待整理/爱乐团/放开.mp3", "filename": "www.snowice.net-爱乐团 - 01.放开.mp3", "title": "", "artist": "", "album": "", "genre": "", "year": "", "track": ""}]
输出：[{"title": "放开", "artist": "爱乐团", "album": "", "genre": "流行", "year": "", "track": "1", "confidence": 0.85, "note": "站点前缀是噪音；01. 是音轨号"}]

示例 6（动态歌词水印 + 副标题 + —演唱者后缀 + 歌词引用）：
输入：[{"path": "待整理/程响/世界这么大还是遇见你.mp3", "filename": "【動態歌詞】世界這麼大還是遇見你(清新的小女孩（中文版）)—程響『世界這麼大還是遇見你 多少次瘋狂多少天真』.mp3", "title": "【動態歌詞】世界這麼大還是遇見你(清新的小女孩（中文版）)—程響『世界這麼大還是遇見你 多少次瘋狂多少天真』", "artist": "", "album": "", "genre": "", "year": "", "track": ""}]
输出：[{"title": "世界这么大还是遇见你", "artist": "程响", "album": "", "genre": "流行", "year": "", "track": "", "confidence": 0.85, "note": "去掉动态歌词标记/副标题/歌词引用；—后缀拆出演唱者；繁转简"}]
"""


def verify_tags(
    items: list[dict],
    config: LLMConfig,
    *,
    cache_dir: str | Path | None = None,
    request_fn=None,
    progress=None,
    stats: dict | None = None,
) -> list[dict]:
    """大模型优先校对标签：路径 + 文件名 + 现有标签 → 规范的 title/artist/album/genre/year/track。

    items 每项含 path/filename/title/artist/album/genre/year/track。
    批量查询、按整项内容缓存（llm_verify.json），返回与 items 一一对应的结果。
    progress(done, total, label)：每批请求前回调，label 为本批第一首歌的文件名。
    失败时抛 LLMError；调用前请确认 config.is_configured()。
    """
    keys = [json.dumps(
        {k: (it.get(k) or "") for k in
         ("path", "filename", "title", "artist", "album",
          "genre", "year", "track")},
        ensure_ascii=False, sort_keys=True) for it in items]
    name_of = {k: (it.get("filename") or "") for k, it in zip(keys, items)}
    return _query_cached(keys, config, VERIFY_SYSTEM_PROMPT, "llm_verify.json",
                         cache_dir=cache_dir, request_fn=request_fn,
                         progress=progress, stats=stats,
                         label_fn=lambda k: name_of.get(k) or k)
