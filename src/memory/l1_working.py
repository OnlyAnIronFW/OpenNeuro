"""L1 工作记忆 — 单场直播的实时上下文"""

import time
from collections import deque, OrderedDict
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class WorkingMemory:
    """L1 工作记忆: 纯内存, 单场直播生命周期, <1ms 读写"""

    # ── 弹幕环形缓冲 ──
    _messages: deque = field(default_factory=lambda: deque(maxlen=50))

    # ── 去重: 已回复的消息ID ──
    _replied_ids: OrderedDict = field(default_factory=OrderedDict)

    # ── 决策历史 ──
    _decisions: deque = field(default_factory=lambda: deque(maxlen=10))

    # ── 发言控制 ──
    last_reply_at: float = 0.0
    reply_count: int = 0

    # ── 话题追踪 ──
    current_topic: str = ""
    topic_started_at: float = 0.0
    topic_msg_count: int = 0

    # ── 视觉 ──
    visual_available: bool = True
    last_visual_summary: str = ""
    last_visual_update: float = 0.0

    # ── 内容策略信号 ──
    content_strategy: Dict[str, Any] = field(default_factory=dict)

    # ── 消息操作 ──────────────────────────────────────

    def add_message(self, msg: Dict[str, Any]) -> None:
        self._messages.append({
            "user": msg.get("user", "?"),
            "text": msg.get("text", ""),
            "mentioned_bot": msg.get("mentioned_bot", False),
            "is_question": msg.get("is_question", False),
            "event_type": msg.get("event_type", ""),
            "timestamp": msg.get("timestamp", time.time()),
        })

    @property
    def recent_messages(self) -> List[Dict]:
        return list(self._messages)

    @property
    def pending_count(self) -> int:
        """尚未回复的新消息数 (简化: 用消息数近似)"""
        return len(self._messages)

    # ── 去重 ──────────────────────────────────────────

    def is_replied(self, msg_id: str) -> bool:
        return msg_id in self._replied_ids

    def mark_replied(self, msg_id: str) -> None:
        self._replied_ids[msg_id] = time.time()
        while len(self._replied_ids) > 200:
            self._replied_ids.popitem(last=False)

    # ── 决策记录 ──────────────────────────────────────

    def add_decision(self, token: str, confidence: Optional[float] = None) -> None:
        self._decisions.append({
            "token": token,
            "confidence": confidence,
            "timestamp": time.time(),
        })

    @property
    def recent_decisions(self) -> List[Dict]:
        return list(self._decisions)

    # ── 发言控制 ──────────────────────────────────────

    def record_reply(self) -> None:
        self.last_reply_at = time.time()
        self.reply_count += 1

    @property
    def seconds_since_last_reply(self) -> float:
        if not self.last_reply_at:
            return 999.0
        return time.time() - self.last_reply_at

    # ── 话题 ──────────────────────────────────────────

    def update_topic(self, topic: str) -> None:
        if topic != self.current_topic:
            self.current_topic = topic
            self.topic_started_at = time.time()
            self.topic_msg_count = 1
        else:
            self.topic_msg_count += 1

    # ── 序列化 (供 prompt 拼装) ───────────────────────

    def to_context(self) -> Dict[str, Any]:
        return {
            "recent_messages": list(self._messages)[-20:],
            "current_topic": self.current_topic,
            "seconds_since_last_reply": self.seconds_since_last_reply,
            "visual_summary": self.last_visual_summary,
            "content_strategy": self.content_strategy,
            "reply_count": self.reply_count,
        }

    # ── 快照 (容灾) ───────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        return {
            "messages_count": len(self._messages),
            "replied_count": len(self._replied_ids),
            "decisions": list(self._decisions),
            "current_topic": self.current_topic,
            "last_reply_at": self.last_reply_at,
            "reply_count": self.reply_count,
        }

    def reset(self) -> None:
        self._messages.clear()
        self._replied_ids.clear()
        self._decisions.clear()
        self.last_reply_at = 0.0
        self.reply_count = 0
        self.current_topic = ""
        self.topic_msg_count = 0
