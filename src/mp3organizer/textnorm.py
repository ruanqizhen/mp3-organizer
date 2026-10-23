"""中文文本规范：繁转简（OpenCC t2s），缺库时原样返回。"""
from __future__ import annotations

_converters: dict = {}


def has_cjk(text: str) -> bool:
    """是否包含汉字（CJK 统一表意文字及扩展，不含假名/谚文）。"""
    for ch in text:
        o = ord(ch)
        if (
            0x4E00 <= o <= 0x9FFF
            or 0x3400 <= o <= 0x4DBF
            or 0xF900 <= o <= 0xFAFF
        ):
            return True
    return False


def has_other_script(text: str) -> bool:
    """是否包含假名/谚文/西里尔/泰文/阿拉伯文/天城文等非拉丁非汉字字母。"""
    for ch in text:
        o = ord(ch)
        if (
            0x3040 <= o <= 0x30FF  # 平假名/片假名
            or 0xAC00 <= o <= 0xD7AF  # 谚文音节
            or 0x1100 <= o <= 0x11FF  # 谚文素
            or 0x0400 <= o <= 0x04FF  # 西里尔
            or 0x0E00 <= o <= 0x0E7F  # 泰文
            or 0x0600 <= o <= 0x06FF  # 阿拉伯文
            or 0x0900 <= o <= 0x097F  # 天城文
        ):
            return True
    return False


def to_simplified(text: str) -> str:
    """中文转简体；无汉字、日韩等非拉丁文字、或缺 OpenCC 时原样返回。"""
    if not text or not has_cjk(text) or has_other_script(text):
        return text
    try:
        import opencc
    except ImportError:
        return text
    cc = _converters.get("t2s")
    if cc is None:
        cc = _converters["t2s"] = opencc.OpenCC("t2s")
    try:
        return cc.convert(text)
    except Exception:  # noqa: BLE001 - 转换失败不影响主流程，原样返回
        return text
