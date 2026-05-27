"""文本工具"""

import re


def count_chinese(text: str) -> int:
    """统计中文字符数量"""
    return sum(1 for c in text if '一' <= c <= '鿿')


def count_english_words(text: str) -> int:
    """统计英文单词数量"""
    return len(re.findall(r'[a-zA-Z]+', text))


def detect_language(text: str) -> str:
    """简单语言检测"""
    if not text.strip():
        return "unknown"
    cn = count_chinese(text)
    en = count_english_words(text)
    if cn > en * 3:
        return "zh"
    elif en > cn * 3:
        return "en"
    return "mixed"


def truncate_at_sentence(text: str, max_len: int) -> str:
    """在完整句子处截断"""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    for sep in ['。', '！', '？', '!', '?', '.']:
        last = truncated.rfind(sep)
        if last > max_len * 0.5:
            return truncated[:last + 1]
    return truncated


def strip_action_brackets(text: str) -> str:
    """去掉括号内的动作描述 (≤8字视为动作)"""
    def replacer(match):
        inner = match.group(1)
        if len(inner) <= 8:
            return ''
        return match.group(0)
    text = re.sub(r'\(([^)]{0,8})\)', replacer, text)
    text = re.sub(r'（([^）]{0,8})）', replacer, text)
    return ' '.join(text.split())
