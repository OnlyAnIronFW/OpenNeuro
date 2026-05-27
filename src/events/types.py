"""事件类型定义"""

from dataclasses import dataclass, field
from typing import Any, Dict

# ── 事件类型常量 ──────────────────────────────────────────

class EventType:
    # 输入事件
    PLATFORM_MESSAGE_RECEIVED = "platform.message.received"
    PLATFORM_GIFT_RECEIVED = "platform.gift.received"
    PLATFORM_SUBSCRIPTION_RECEIVED = "platform.subscription.received"
    VISUAL_FRAME_PROCESSED = "visual.frame.processed"
    VISUAL_EVENT_DETECTED = "visual.event.detected"
    AUDIO_SPEECH_DETECTED = "audio.speech.detected"

    # 决策事件
    S1_DECISION_MADE = "s1.decision.made"
    S1_DECISION_OVERRIDDEN = "s1.decision.overridden"
    S2_REQUEST_SENT = "s2.request.sent"
    S2_RESPONSE_RECEIVED = "s2.response.received"
    REPLY_SENT = "reply.sent"

    # 线程事件
    THREAD_CREATED = "thread.created"
    THREAD_MERGED = "thread.merged"
    THREAD_CLOSED = "thread.closed"
    THREAD_STARVATION_DETECTED = "thread.starvation.detected"

    # 系统事件
    SYSTEM_DEGRADATION_CHANGED = "system.degradation.level_changed"
    SYSTEM_HEALTH_CHECK = "system.health.check"
    SESSION_STARTED = "session.started"
    SESSION_ENDED = "session.ended"


@dataclass
class Event:
    """统一事件对象"""
    event_id: str
    timestamp: float
    type: str
    source: str
    payload: Dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
