"""安全加固 — 提示注入防御 + 行为偏离检测"""

import re
import hashlib
from typing import Dict, List, Optional


class InputSanitizer:
    """输入清洗 — 防止提示注入"""

    # 危险Token模式
    DANGEROUS_PATTERNS = [
        (re.compile(r'<\s*\|?\s*(im_start|im_end|system|instruction)\s*\|?\s*>', re.IGNORECASE),
         "[blocked]"),
        (re.compile(r'(ignore|forget|disregard).{0,30}(instructions|prompts|rules)',
                    re.IGNORECASE), "[blocked]"),
        (re.compile(r'you\s+are\s+(now|no\s+longer)\s+a\s+(different|new)', re.IGNORECASE),
         "[blocked]"),
        (re.compile(r'<script|<iframe|javascript:', re.IGNORECASE), "[blocked]"),
    ]

    @classmethod
    def sanitize(cls, text: str) -> str:
        """清洗用户输入, 替换危险模式"""
        if not text:
            return ""
        for pattern, replacement in cls.DANGEROUS_PATTERNS:
            text = pattern.sub(replacement, text)
        return text[:500]  # 截断超长输入


class BehaviorMonitor:
    """行为偏离检测 — 监控S2输出是否异常偏离人设"""

    def __init__(self, window_size: int = 20):
        self._window_size = window_size
        self._output_hashes: List[str] = []
        self._deviation_count: int = 0
        self._alert_threshold: int = 3

    def observe(self, text: str) -> Optional[str]:
        """观察一次输出, 返回告警信息 (如有)"""
        if not text:
            return None

        h = hashlib.md5(text.encode()).hexdigest()

        # 检测重复输出 (可能的死循环)
        if h in self._output_hashes[-5:]:
            self._deviation_count += 1
        else:
            self._deviation_count = max(0, self._deviation_count - 1)

        self._output_hashes.append(h)
        if len(self._output_hashes) > self._window_size:
            self._output_hashes = self._output_hashes[-self._window_size:]

        if self._deviation_count >= self._alert_threshold:
            return f"重复输出检测: 连续{self._deviation_count}次相同回复"

        # 检测极端短/长
        if len(text) < 2 and len(self._output_hashes) > 3:
            return "异常短回复"

        return None

    def reset(self) -> None:
        self._output_hashes.clear()
        self._deviation_count = 0
