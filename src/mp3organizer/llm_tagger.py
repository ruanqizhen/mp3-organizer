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
    batch_size: int = 20
    timeout: int = 60

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
3. 发挥你的中文流行音乐知识：拼音缩写、错别字、顺序颠倒都要尽力还原。
4. 实在看不出的文件名：title 填去掉后缀的文件名，artist/album/genre/year 填空字符串，confidence 填 0.1，不要编造演唱者。

示例：
输入：["周杰伦 - 夜曲.mp3", "yq_zjl.mp3", "未知歌曲.mp3"]
输出：[{"title": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦", "genre": "流行", "year": "2005", "confidence": 0.95}, {"title": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦", "genre": "流行", "year": "2005", "confidence": 0.7}, {"title": "未知歌曲", "artist": "", "album": "", "genre": "", "year": "", "confidence": 0.1}]
"""


def _cache_path(cache_dir: str | Path | None, cache_name: str) -> Path:
    if cache_dir is None:
        cache_dir = Path.home() / ".cache" / "mp3-organizer"
    return Path(cache_dir) / cache_name


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
) -> list[dict]:
    """批量查询，带本地缓存。返回与 filenames 一一对应的结果 dict 列表。

    request_fn(url, headers, payload, timeout) -> 响应 JSON（dict），供测试注入。
    失败时抛 LLMError；调用前请确认 config.is_configured()。
    """
    if not config.is_configured():
        raise LLMError("大模型未配置：需要 API 地址和模型名")
    cache = _load_cache(cache_dir, cache_name)
    todo = [f for f in filenames if f not in cache]
    bs = max(1, config.batch_size)
    for i in range(0, len(todo), bs):
        batch = todo[i:i + bs]
        results = _query_batch(batch, config, system_prompt, request_fn)
        for name, item in zip(batch, results):
            cache[name] = item
        _save_cache(cache_dir, cache_name, cache)
    return [cache.get(f, {}) for f in filenames]


def query_llm(
    filenames: list[str],
    config: LLMConfig,
    *,
    cache_dir: str | Path | None = None,
    request_fn=None,
) -> list[dict]:
    """猜标签：文件名 → 标题/演唱者/专辑/类型/年份/置信度。"""
    return _query_cached(filenames, config, SYSTEM_PROMPT, "llm_tags.json",
                         cache_dir=cache_dir, request_fn=request_fn)


RENAME_SYSTEM_PROMPT = """你是文件名整理助手。用户会给你一批 MP3 文件名（JSON 数组），每个都很长很乱，夹杂着各种无关信息。
请找出每首歌真正的"演唱者"和"曲名"，忽略码率标记（如 320K/FLAC）、网址、"免费下载"、括号里的备注、开头序号等噪音。

要求：
1. 只返回 JSON 数组，与输入顺序一一对应，不要输出其他任何内容。
2. 每项包含字段：artist（演唱者/演奏者，未知填空字符串）、title（曲名）、confidence（0~1，你对这次解析的把握）。
3. 发挥你的中文流行音乐知识：拼音缩写、错别字、顺序颠倒都要尽力还原。
4. 实在看不出的文件名：title 填去掉噪音后最像曲名的部分，artist 填空字符串，confidence 填 0.1，不要编造演唱者。

示例：
输入：["[320K]周杰伦-夜曲(官方正式版)_www.xiami.com免费下载.mp3", "03. 王菲 - 红豆 (Live现场版).mp3"]
输出：[{"artist": "周杰伦", "title": "夜曲", "confidence": 0.95}, {"artist": "王菲", "title": "红豆", "confidence": 0.9}]
"""


def parse_filenames(
    filenames: list[str],
    config: LLMConfig,
    *,
    cache_dir: str | Path | None = None,
    request_fn=None,
) -> list[dict]:
    """解析杂乱文件名 → [{artist, title, confidence}]，用于整理标准文件名。"""
    return _query_cached(filenames, config, RENAME_SYSTEM_PROMPT, "llm_rename.json",
                         cache_dir=cache_dir, request_fn=request_fn)


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
) -> list[dict]:
    """演唱者 → 语言.性别分类。pairs 为 [(artist, title)]，按演唱者缓存。"""
    if not config.is_configured():
        raise LLMError("大模型未配置：需要 API 地址和模型名")
    keys = [normalize_artist(a) or ("title:" + t.strip().casefold())
            for a, t in pairs]
    cache = _load_cache(cache_dir, "llm_classify.json")
    seen = set(cache)
    todo = []
    for i, k in enumerate(keys):
        if k not in seen:  # 同一批里同一演唱者只问一次
            seen.add(k)
            todo.append(i)
    bs = max(1, config.batch_size)
    for s in range(0, len(todo), bs):
        batch_idx = todo[s:s + bs]
        batch = [{"artist": pairs[i][0], "title": pairs[i][1]}
                 for i in batch_idx]
        results = _query_batch(batch, config, CLASSIFY_SYSTEM_PROMPT, request_fn)
        for i, item in zip(batch_idx, results):
            cache[keys[i]] = item
        _save_cache(cache_dir, "llm_classify.json", cache)
    return [cache.get(k, {}) for k in keys]
