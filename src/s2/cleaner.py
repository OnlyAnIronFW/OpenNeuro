"""S2 输出清洗器 — 5级管道"""

import json
import re
from dataclasses import dataclass, field
from typing import Tuple, List


@dataclass
class CleanResult:
    text: str
    warnings: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


class S2OutputCleaner:
    """
    5 级清洗管道:
      Step 1: JSON 包装剥离
      Step 2: 括号/动作描述剥离 (≤8字)
      Step 3: 元文本剥离
      Step 4: 语言验证
      Step 5: 长度控制 (>80字截断)

    无冗余: 每步输入等同于上一步输出, 不重复处理
    """

    # Step 2: 动作描述匹配
    _BRACKET_RE = re.compile(
        r"[\(（][^)）]{0,8}[\)）]"  # 中英文括号, 内容0-8字
    )
    _ASTERISK_RE = re.compile(r"\*[^*]{0,8}\*")  # *动作*
    _ANGLE_RE = re.compile(r"<[^>]{0,8}>")  # <动作>

    # Step 3: 元文本前缀
    _META_PREFIXES = [
        re.compile(r"^(根据|按照|基于|参考).{0,20}(分析|判断|认为)"),
        re.compile(r"^(让我|我来|先).{0,10}(思考|分析|想想)"),
        re.compile(r"^(作为|身为).{0,15}(我|AI|助手)"),
        re.compile(r"^(以下是|这是我的|回复[：:])"),
        re.compile(r"^我(应该|可以|会|能).{0,15}(回复|回答|说)"),
        re.compile(r"^(好的|好|OK)[，,].{0,15}(来看看|分析|总结|回复)"),
    ]

    # ── 公共接口 ──────────────────────────────────────

    def clean(self, raw: str, expected_language: str = "zh") -> CleanResult:
        """完整清洗管道"""
        text = raw.strip() if raw else ""
        warnings = []

        # Step 1
        text, w = self._strip_json(text)
        if w:
            warnings.append(w)

        # Step 2
        text = self._strip_actions(text)

        # Step 3
        text, w = self._strip_meta(text)
        if w:
            warnings.append(w)

        # Step 4
        if not self._check_language(text, expected_language):
            warnings.append("language_mismatch")

        # Step 5
        if len(text) > 80:
            text = self._truncate(text, 80)
            warnings.append("truncated")

        # 空检查
        if not text.strip():
            warnings.append("empty_after_clean")

        return CleanResult(text=text.strip(), warnings=warnings)

    # ── Step 1: JSON ──────────────────────────────────

    @staticmethod
    def _strip_json(text: str) -> Tuple[str, str]:
        """去掉 ```json ... ``` 包装或裸JSON解析"""
        t = text.strip()

        # ``` 包装
        if t.startswith("```"):
            t = re.sub(r"^```\w*\s*", "", t)
            t = re.sub(r"\s*```$", "", t)
            t = t.strip()

        # 尝试 JSON parse
        try:
            data = json.loads(t)
            for key in ("reply", "text", "content", "response", "回复"):
                if key in data and isinstance(data[key], str):
                    return data[key].strip(), "json_unwrapped"
        except (json.JSONDecodeError, TypeError):
            pass

        # 去掉 "回复:" / "Reply:" 前缀
        t = re.sub(r"^(回复|Reply|Response|输出)[：:]\s*", "", t, flags=re.IGNORECASE)

        return t.strip(), ""

    # ── Step 2: 动作描述 ──────────────────────────────

    @classmethod
    def _strip_actions(cls, text: str) -> str:
        """去掉括号/星号/尖括号内的短动作描述"""
        text = cls._BRACKET_RE.sub("", text)
        text = cls._ASTERISK_RE.sub("", text)
        text = cls._ANGLE_RE.sub("", text)
        return " ".join(text.split())

    # ── Step 3: 元文本 ────────────────────────────────

    @classmethod
    def _strip_meta(cls, text: str) -> Tuple[str, str]:
        """去掉AI自我反思的元文本前缀"""
        for pat in cls._META_PREFIXES:
            m = pat.match(text)
            if m:
                idx = text.find("。")
                if 0 < idx < 50:
                    return text[idx + 1 :].strip(), "meta_stripped"
                else:
                    # 找不到句号 → 整句可能被误伤; 保留原文, 标记警告
                    return text, "meta_stripped_to_empty_guarded"
        return text, ""

    # ── Step 4: 语言 ──────────────────────────────────

    @staticmethod
    def _check_language(text: str, expected: str) -> bool:
        if not text:
            return True
        cn = sum(1 for c in text if "一" <= c <= "鿿")
        en = len(re.findall(r"[a-zA-Z]+", text))
        ratio = cn / max(len(text), 1)
        if expected == "zh":
            return ratio > 0.2
        elif expected == "en":
            return ratio < 0.3
        return True

    # ── Step 5: 长度 ──────────────────────────────────

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        truncated = text[:max_len]
        for sep in ("。", "！", "？", "!", "?", "."):
            last = truncated.rfind(sep)
            if last > max_len * 0.5:
                return truncated[: last + 1]
        return truncated
