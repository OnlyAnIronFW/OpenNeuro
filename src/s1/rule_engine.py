"""S1 规则引擎 — 决策二次校验 + 看门狗"""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .parser import S1Token, ParsedDecision


# ── 时间窗口: 不可变配置快照 ──────────────────────────

@dataclass(frozen=True)
class RuleConfig:
    protection_period_ms: int = 2000
    max_replies_per_10s: int = 3
    silence_watchdog_ms: int = 60000
    forced_cooldown_ms: int = 5000
    speak_priority_threshold: float = 0.5
    quick_reply_max_chars: int = 15


class RuleEngine:
    """
    S1 决策二次校验。

    检查顺序:
      1. 保护期       — 距上次发言 < N ms → 拦截
      2. 频率限制     — 10秒内 > N 次 → 拦截
      3. 连续Token    — 连续3次相同 → 强制沉默
      4. QuickReply超长 — > N 字 → 升级为 Start-Speaking
      5. 看门狗       — 超过 N ms 未发言 + 有待处理消息 → 紧急决策

    拦截的决策被改写为 Continue-Listening, 不是丢弃。
    """

    def __init__(self, config: Optional[RuleConfig] = None):
        self._cfg = config or RuleConfig()
        self._reply_timestamps: deque[float] = deque()
        self._last_reply_at: float = 0.0
        self._consecutive_tokens: List[S1Token] = []
        self._silence_start: float = time.time()
        self._visual_available: bool = True
        self._overrides: List[dict] = []  # 拦截日志

    # ── 公共接口 ──────────────────────────────────────

    def validate(
        self,
        parsed: ParsedDecision,
        current_time: Optional[float] = None,
    ) -> ParsedDecision:
        """
        校验并可能改写决策。
        返回的可能是原决策或 Continue-Listening。
        """
        now = current_time or time.time()

        # 非回复型 Token 直接放行 (不触发保护期/频率检查)
        if parsed.token not in (S1Token.QUICK_REPLY, S1Token.START_SPEAKING):
            self._track_consecutive(parsed.token)
            return parsed

        # ── 检查 1: 保护期 ──
        if self._is_in_protection(now):
            self._log_override(parsed, "protection_period", now)
            return self._make_silent(parsed, "protection_period")

        # ── 检查 2: 频率限制 ──
        if self._is_rate_limited(now):
            self._log_override(parsed, "rate_limit", now)
            return self._make_silent(parsed, "rate_limit")

        # ── 检查 3: 连续 Token ──
        self._track_consecutive(parsed.token)
        if self._is_loop_detected():
            self._consecutive_tokens.clear()
            self._log_override(parsed, "consecutive_loop", now)
            return self._make_silent(parsed, "consecutive_loop")

        # ── 检查 4: QuickReply 超长 → 升级 ──
        if parsed.token == S1Token.QUICK_REPLY and parsed.quick_reply_text:
            if len(parsed.quick_reply_text) > self._cfg.quick_reply_max_chars:
                return ParsedDecision(
                    token=S1Token.START_SPEAKING,
                    confidence=0.6,
                    direction=parsed.quick_reply_text,
                    parse_warnings=parsed.parse_warnings + ["upgraded: quick_reply_too_long"],
                    raw_output=parsed.raw_output,
                )

        return parsed

    # ── 记录 ──────────────────────────────────────────

    def record_reply(self, timestamp: Optional[float] = None):
        """在 AI 实际发言后调用"""
        now = timestamp or time.time()
        self._reply_timestamps.append(now)
        self._last_reply_at = now
        self._silence_start = now  # 重置沉默计时

    def set_visual_available(self, available: bool):
        self._visual_available = available

    # ── 看门狗 ────────────────────────────────────────

    @property
    def seconds_since_last_reply(self) -> float:
        return time.time() - self._last_reply_at if self._last_reply_at else 999.0

    def is_silent_too_long(self, current_time: Optional[float] = None) -> bool:
        now = current_time or time.time()
        return (now - self._silence_start) * 1000 > self._cfg.silence_watchdog_ms

    def emergency_decision(self, messages: List[dict]) -> ParsedDecision:
        """
        看门狗触发时的紧急决策:
        检查是否有必须回复的消息 (@/问题/礼物)
        """
        for msg in messages:
            if msg.get("mentioned_bot"):
                return ParsedDecision(
                    token=S1Token.START_SPEAKING,
                    confidence=0.9,
                    direction=f"紧急回复 {msg.get('user','?')} 的 @消息",
                    parse_warnings=["emergency: watchdog"],
                )
            if msg.get("is_question"):
                return ParsedDecision(
                    token=S1Token.START_SPEAKING,
                    confidence=0.75,
                    direction=f"紧急回答 {msg.get('user','?')} 的问题",
                    parse_warnings=["emergency: watchdog_question"],
                )
            if msg.get("event_type") == "gift":
                return ParsedDecision(
                    token=S1Token.QUICK_REPLY,
                    quick_reply_text=f"谢谢{msg.get('user','?')}的礼物~",
                    parse_warnings=["emergency: watchdog_gift"],
                )

        return ParsedDecision(
            token=S1Token.CONTINUE_LISTENING,
            parse_warnings=["emergency: watchdog_no_action"],
        )

    # ── 重置 ──────────────────────────────────────────

    def reset(self):
        self._reply_timestamps.clear()
        self._last_reply_at = 0.0
        self._consecutive_tokens.clear()
        self._silence_start = time.time()
        self._overrides.clear()

    @property
    def override_log(self) -> List[dict]:
        return list(self._overrides)

    # ── 内部检查 ──────────────────────────────────────

    def _is_in_protection(self, now: float) -> bool:
        if self._last_reply_at <= 0:
            return False
        return (now - self._last_reply_at) * 1000 < self._cfg.protection_period_ms

    def _is_rate_limited(self, now: float) -> bool:
        cutoff = now - 10.0
        while self._reply_timestamps and self._reply_timestamps[0] < cutoff:
            self._reply_timestamps.popleft()
        return len(self._reply_timestamps) >= self._cfg.max_replies_per_10s

    def _track_consecutive(self, token: S1Token):
        self._consecutive_tokens.append(token)
        if len(self._consecutive_tokens) > 3:
            self._consecutive_tokens.pop(0)

    def _is_loop_detected(self) -> bool:
        return (
            len(self._consecutive_tokens) == 3
            and len(set(self._consecutive_tokens)) == 1
            and self._consecutive_tokens[0]
            in (S1Token.QUICK_REPLY, S1Token.START_SPEAKING)
        )

    # ── 辅助 ──────────────────────────────────────────

    def _make_silent(self, original: ParsedDecision, reason: str) -> ParsedDecision:
        return ParsedDecision(
            token=S1Token.CONTINUE_LISTENING,
            parse_warnings=original.parse_warnings + [f"overridden: {reason}"],
            raw_output=original.raw_output,
        )

    def _log_override(self, parsed: ParsedDecision, reason: str, now: float):
        self._overrides.append({
            "timestamp": now,
            "original_token": parsed.token.value,
            "reason": reason,
        })
        if len(self._overrides) > 100:
            self._overrides.pop(0)
