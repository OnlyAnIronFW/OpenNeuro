"""S1 输出解析器 — 正则匹配 + Levenshtein 模糊容错 + 降级"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Tuple

import Levenshtein


class S1Token(Enum):
    QUICK_REPLY = "Quick-Reply"
    START_SPEAKING = "Start-Speaking"
    CONTINUE_LISTENING = "Continue-Listening"
    START_LISTENING = "Start-Listening"
    CONTINUE_SPEAKING = "Continue-Speaking"
    CANCEL_S2 = "Cancel-S2"


# 降级状态机
class ParserState(Enum):
    NORMAL = "normal"          # 正常
    DEGRADED = "degraded"     # 连续2次失败, 告警
    FAILED = "failed"          # 连续5次失败, 需人工介入


@dataclass
class ParsedDecision:
    token: S1Token
    confidence: Optional[float] = None
    quick_reply_text: Optional[str] = None
    direction: Optional[str] = None
    thread_id: Optional[str] = None
    raw_output: str = ""
    parse_warnings: List[str] = field(default_factory=list)

    @property
    def is_reply(self) -> bool:
        return self.token in (S1Token.QUICK_REPLY, S1Token.START_SPEAKING)

    @property
    def is_silence(self) -> bool:
        return self.token in (
            S1Token.CONTINUE_LISTENING,
            S1Token.START_LISTENING,
            S1Token.CONTINUE_SPEAKING,
        )


class S1Parser:
    """
    MiniCPM 输出解析器。

    三层解析:
      1. 精确正则匹配 (6 种 Token)
      2. Levenshtein 模糊匹配 (距离 ≤2)
      3. 无法解析 → 默认 Continue-Listening + 降级追踪
    """

    FUZZY_THRESHOLD = 2
    MAX_CONSECUTIVE_FAILURES_FOR_DEGRADED = 2
    MAX_CONSECUTIVE_FAILURES_FOR_FAILED = 5
    RECOVERY_STREAK = 10

    # Token 候选名称 (模糊匹配用)
    TOKEN_CANDIDATES = {
        S1Token.QUICK_REPLY: [
            "Quick-Reply", "quick-reply", "quickreply", "QuickReply",
            "quick_reply", "Quick Reply", "quick reply",
        ],
        S1Token.START_SPEAKING: [
            "Start-Speaking", "start-speaking", "startspeaking",
            "StartSpeaking", "start_speaking", "Start Speaking",
        ],
        S1Token.CONTINUE_LISTENING: [
            "Continue-Listening", "continue-listening", "continuelistening",
            "ContinueListening", "continue_listening", "Continue Listening",
            "Continue-Listen",
        ],
        S1Token.START_LISTENING: [
            "Start-Listening", "start-listening", "startlistening",
            "StartListening", "start_listening", "Start Listening",
            "Start-Listen",
        ],
        S1Token.CONTINUE_SPEAKING: [
            "Continue-Speaking", "continue-speaking", "continuespeaking",
            "ContinueSpeaking", "continue_speaking", "Continue Speaking",
            "Continue-Speak",
        ],
        S1Token.CANCEL_S2: [
            "Cancel-S2", "cancel-s2", "cancels2", "CancelS2",
            "cancel_s2", "Cancel S2",
        ],
    }

    def __init__(self):
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._state = ParserState.NORMAL

        # 编译精确正则
        self._patterns: dict[S1Token, re.Pattern] = {
            S1Token.QUICK_REPLY: re.compile(
                r'<\s*\|?\s*Quick-?Reply\s*\|?\s*>\s*(.+)',
                re.IGNORECASE | re.DOTALL,
            ),
            S1Token.START_SPEAKING: re.compile(
                r'<\s*\|?\s*Start-?Speaking'
                r'(?:\s+confidence\s*=\s*([0-9.]+))?'
                r'\s*\|?\s*>\s*(.*)',
                re.IGNORECASE | re.DOTALL,
            ),
            S1Token.CONTINUE_LISTENING: re.compile(
                r'<\s*\|?\s*Continue-?Listen(?:ing)?\s*\|?\s*>',
                re.IGNORECASE,
            ),
            S1Token.START_LISTENING: re.compile(
                r'<\s*\|?\s*Start-?Listen(?:ing)?\s*\|?\s*>',
                re.IGNORECASE,
            ),
            S1Token.CONTINUE_SPEAKING: re.compile(
                r'<\s*\|?\s*Continue-?Speak(?:ing)?\s*\|?\s*>',
                re.IGNORECASE,
            ),
            S1Token.CANCEL_S2: re.compile(
                r'<\s*\|?\s*Cancel-?S2\s*\|?\s*>',
                re.IGNORECASE,
            ),
        }

    # ── 公共接口 ──────────────────────────────────────

    def parse(self, raw: str) -> ParsedDecision:
        """解析 MiniCPM 原始输出"""
        raw = raw.strip() if raw else ""

        # 空输出
        if not raw:
            result = ParsedDecision(
                token=S1Token.CONTINUE_LISTENING,
                parse_warnings=["empty_output"],
                raw_output=raw,
            )
            self._record_failure()
            return result

        # 精确匹配
        for token, pattern in self._patterns.items():
            m = pattern.search(raw)
            if m:
                self._record_success()
                return self._build_parsed(token, m, raw)

        # 模糊匹配
        fuzzy_result = self._fuzzy_match(raw)
        if fuzzy_result:
            self._record_success()
            return fuzzy_result

        # 完全无法解析
        self._record_failure()
        return ParsedDecision(
            token=S1Token.CONTINUE_LISTENING,
            parse_warnings=["unparseable"],
            raw_output=raw,
        )

    # ── 状态 ──────────────────────────────────────────

    @property
    def state(self) -> ParserState:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reset_state(self) -> None:
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._state = ParserState.NORMAL

    # ── 内部: 精确匹配 ────────────────────────────────

    def _build_parsed(
        self, token: S1Token, match: re.Match, raw: str
    ) -> ParsedDecision:
        if token == S1Token.QUICK_REPLY:
            return ParsedDecision(
                token=token,
                quick_reply_text=match.group(1).strip(),
                raw_output=raw,
            )
        elif token == S1Token.START_SPEAKING:
            conf_str = match.group(1)
            direction = match.group(2).strip() if match.lastindex and match.lastindex >= 2 else ""
            return ParsedDecision(
                token=token,
                confidence=float(conf_str) if conf_str else 0.7,
                direction=direction,
                raw_output=raw,
            )
        else:
            return ParsedDecision(token=token, raw_output=raw)

    # ── 内部: 模糊匹配 ────────────────────────────────

    def _fuzzy_match(self, raw: str) -> Optional[ParsedDecision]:
        """对 raw 提取的 Token 名做 Levenshtein 模糊匹配"""

        # 提取 <|...|> 中间的部分作为 token 名
        token_name = self._extract_token_name(raw)

        best_token: Optional[S1Token] = None
        best_dist: int = 999

        for token, candidates in self.TOKEN_CANDIDATES.items():
            for cand in candidates:
                dist = Levenshtein.distance(token_name, cand.lower())
                if dist < best_dist:
                    best_dist = dist
                    best_token = token

        if best_token and best_dist <= self.FUZZY_THRESHOLD:
            result = ParsedDecision(
                token=best_token,
                parse_warnings=[f"fuzzy_match dist={best_dist}"],
                raw_output=raw,
            )
            if best_token == S1Token.START_SPEAKING:
                after = raw[raw.find(">") + 1:].strip() if ">" in raw else ""
                result.direction = after
                result.confidence = self._extract_confidence(raw)
            elif best_token == S1Token.QUICK_REPLY:
                result.quick_reply_text = raw[raw.find(">") + 1:].strip() if ">" in raw else raw
            return result

        return None

    @staticmethod
    def _extract_token_name(raw: str) -> str:
        """从 <|TokenName|> 中提取 TokenName 部分"""
        # 匹配 <| 和 |> 之间的内容
        m = re.search(r'<\s*\|?\s*([^|>]+?)\s*\|?\s*>', raw, re.IGNORECASE)
        if m:
            return m.group(1).strip().lower()
        # 没有 <> 包裹 → 用前30字符
        return raw[:30].lower().strip()

    @staticmethod
    def _extract_confidence(text: str) -> Optional[float]:
        m = re.search(r'confidence\s*=\s*([0-9.]+)', text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return None

    # ── 内部: 降级追踪 ────────────────────────────────

    def _record_success(self):
        self._consecutive_successes += 1
        self._consecutive_failures = 0
        if self._state == ParserState.DEGRADED and \
           self._consecutive_successes >= self.RECOVERY_STREAK:
            self._state = ParserState.NORMAL

    def _record_failure(self):
        self._consecutive_failures += 1
        self._consecutive_successes = 0
        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES_FOR_FAILED:
            self._state = ParserState.FAILED
        elif self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES_FOR_DEGRADED:
            self._state = ParserState.DEGRADED
