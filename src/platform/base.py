"""多平台适配层 — 消息标准化 + 输出过滤"""

import asyncio
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Callable, Awaitable


@dataclass
class UnifiedMessage:
    """标准化消息格式"""

    platform: str = ""
    user: str = ""
    user_id: str = ""
    text: str = ""
    event_type: str = "chat"  # chat | gift | subscription | system
    mentioned_bot: bool = False
    is_question: bool = False
    language: str = "zh"
    monetary_value: float = 0.0
    timestamp: float = field(default_factory=time.time)
    message_id: str = ""
    reply_to_msg_id: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class PlatformAdapter(ABC):
    """平台适配器基类"""

    def __init__(self, platform_name: str):
        self.platform = platform_name
        self._handlers: List[Callable[[UnifiedMessage], Awaitable[None]]] = []

    @abstractmethod
    async def connect(self) -> None:
        """连接平台"""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接"""
        ...

    @abstractmethod
    async def send_message(self, text: str, reply_to: str = "") -> bool:
        """发送消息"""
        ...

    def on_message(self, handler: Callable[[UnifiedMessage], Awaitable[None]]) -> None:
        self._handlers.append(handler)

    async def _emit(self, msg: UnifiedMessage) -> None:
        for h in self._handlers:
            asyncio.create_task(self._safe_invoke(h, msg))

    async def _safe_invoke(self, handler, msg):
        try:
            await handler(msg)
        except Exception:
            pass

    # ── 消息标准化 ────────────────────────────────────

    def normalize(self, raw: Dict[str, Any]) -> UnifiedMessage:
        """原始消息 → UnifiedMessage (子类可覆盖)"""
        text = raw.get("text", "") or ""
        return UnifiedMessage(
            platform=self.platform,
            user=raw.get("user", "anonymous"),
            user_id=raw.get("user_id", raw.get("user", "anon")),
            text=text,
            event_type=raw.get("event_type", "chat"),
            mentioned_bot=("@bot" in text.lower() or raw.get("mentioned", False)),
            is_question=("?" in text or "？" in text),
            language=self._detect_language(text),
            monetary_value=float(raw.get("price", 0)),
            timestamp=raw.get("timestamp", time.time()),
            message_id=raw.get("message_id", ""),
            reply_to_msg_id=raw.get("reply_to", ""),
            raw=raw,
        )

    # ── 输出过滤 ──────────────────────────────────────

    def filter_output(self, text: str) -> str:
        """平台输出过滤 (子类覆盖)"""
        return text

    # ── 工具 ──────────────────────────────────────────

    @staticmethod
    def _detect_language(text: str) -> str:
        if not text:
            return "zh"
        cn = sum(1 for c in text if "一" <= c <= "鿿")
        en = len(re.findall(r"[a-zA-Z]+", text))
        return "zh" if cn > en else "en"
