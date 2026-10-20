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


def _cache_path(cache_dir: str | Path | None) -> Path:
    if cache_dir is None:
        cache_dir = Path.home() / ".cache" / "mp3-organizer"
    return Path(cache_dir) / "llm_tags.json"


def _load_cache(cache_dir: str | Path | None) -> dict:
    try:
        return json.loads(_cache_path(cache_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache_dir: str | Path | None, cache: dict) -> None:
    p = _cache_path(cache_dir)
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
    filenames: list[str], config: LLMConfig, request_fn=None
) -> list[dict]:
    url = config.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.api_key.strip():
        headers["Authorization"] = "Bearer " + config.api_key.strip()
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
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


def query_llm(
    filenames: list[str],
    config: LLMConfig,
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
    cache = _load_cache(cache_dir)
    todo = [f for f in filenames if f not in cache]
    bs = max(1, config.batch_size)
    for i in range(0, len(todo), bs):
        batch = todo[i:i + bs]
        results = _query_batch(batch, config, request_fn)
        for name, item in zip(batch, results):
            cache[name] = item
        _save_cache(cache_dir, cache)
    return [cache.get(f, {}) for f in filenames]
