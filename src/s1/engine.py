"""S1 决策引擎 — MiniCPM + Parser + RuleEngine 编排"""

import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from src.models.s1_client import MiniCPMClient, S1RawResponse
from src.prompts.assembler import PromptAssembler
from src.utils.logger import log_manager

from .parser import S1Parser, S1Token, ParsedDecision, ParserState
from .rule_engine import RuleEngine, RuleConfig

_log = log_manager.get("s1")


@dataclass
class S1DecisionResult:
    """S1 完整决策结果"""
    parsed: ParsedDecision
    raw_s1_output: str
    s1_latency_ms: float
    s1_error: Optional[str] = None
    parser_state: ParserState = ParserState.NORMAL
    overridden: bool = False
    emergency: bool = False
    decision_path: List[str] = field(default_factory=list)


class S1Engine:
    """
    S1 完整决策引擎。

    流程:
      Input → build_context → MiniCPM.decide() → Parser.parse()
            → RuleEngine.validate() → S1DecisionResult

    安全网:
      - MiniCPM 超时/错误 → 回退 Continue-Listening + 告警
      - 看门狗超时 → emergency_decision()
      - Parser 连续失败 → 降级追踪
    """

    def __init__(
        self,
        client: MiniCPMClient,
        prompts: PromptAssembler,
        rule_config: Optional[RuleConfig] = None,
    ):
        self._client = client
        self._prompts = prompts
        self._parser = S1Parser()
        self._rules = RuleEngine(rule_config)
        self._watchdog_enabled = True

    # ── 生命周期 ──────────────────────────────────────

    async def start(self):
        await self._client.start()

    async def stop(self):
        await self._client.stop()

    # ── 核心决策 ──────────────────────────────────────

    async def decide(
        self,
        messages: List[Dict[str, Any]],
        thread_snapshot: Optional[List[Dict[str, Any]]] = None,
        visual_summary: str = "",
        emotional_state: str = "",
        content_strategy: Optional[Dict[str, Any]] = None,
        working_memory: Optional[Dict[str, Any]] = None,
    ) -> S1DecisionResult:
        """
        执行完整 S1 决策

        Args:
            messages: 新消息列表
            thread_snapshot: 线程快照
            visual_summary: 画面摘要
            emotional_state: 情绪描述
            content_strategy: 内容策略信号
            working_memory: 工作记忆状态
        """
        path: List[str] = []
        now = time.time()

        # ── Step 1: 拼装上下文 ──
        user_context = self._build_context(
            messages,
            thread_snapshot or [],
            visual_summary,
            emotional_state,
            content_strategy or {},
            working_memory or {},
        )
        path.append("context_built")

        # ── Step 2: 调 MiniCPM ──
        _log.debug("s1_decide_start", msg_count=len(messages))
        raw = await self._client.decide(
            system_prompt=self._prompts.build_s1_system(),
            user_context=user_context,
            temperature=0.1,
            max_tokens=64,
        )

        if raw.error:
            path.append(f"s1_error: {raw.error}")
            # 回退: 检查是否需要看门狗介入
            if self._watchdog_enabled and self._rules.is_silent_too_long(now):
                emergency = self._rules.emergency_decision(messages)
                path.append("watchdog_triggered")
                return S1DecisionResult(
                    parsed=emergency,
                    raw_s1_output=raw.content,
                    s1_latency_ms=raw.latency_ms,
                    s1_error=raw.error,
                    parser_state=self._parser.state,
                    emergency=True,
                    decision_path=path,
                )

            return S1DecisionResult(
                parsed=ParsedDecision(
                    token=S1Token.CONTINUE_LISTENING,
                    parse_warnings=[f"s1_error: {raw.error}"],
                ),
                raw_s1_output="",
                s1_latency_ms=raw.latency_ms,
                s1_error=raw.error,
                parser_state=self._parser.state,
                decision_path=path,
            )

        path.append(f"s1_raw: {raw.content[:50]}...")

        # ── Step 3: Parser ──
        parsed = self._parser.parse(raw.content)
        path.append(f"parsed: {parsed.token.value}")
        if parsed.parse_warnings:
            path.append(f"warnings: {parsed.parse_warnings}")

        # ── Step 4: 看门狗检查 ──
        if self._watchdog_enabled and self._rules.is_silent_too_long(now):
            # 如果 Parser 解析出的是沉默Token + 看门狗触发 → 紧急决策
            if parsed.token == S1Token.CONTINUE_LISTENING:
                emergency = self._rules.emergency_decision(messages)
                if emergency.token != S1Token.CONTINUE_LISTENING:
                    path.append("watchdog_overrides_silence")
                    return S1DecisionResult(
                        parsed=emergency,
                        raw_s1_output=raw.content,
                        s1_latency_ms=raw.latency_ms,
                        parser_state=self._parser.state,
                        emergency=True,
                        decision_path=path,
                    )

        # ── Step 5: RuleEngine ──
        validated = self._rules.validate(parsed, now)
        overridden = validated.token != parsed.token
        if overridden:
            path.append(f"overridden: {parsed.token.value}→{validated.token.value}")

        return S1DecisionResult(
            parsed=validated,
            raw_s1_output=raw.content,
            s1_latency_ms=raw.latency_ms,
            parser_state=self._parser.state,
            overridden=overridden,
            decision_path=path,
        )

    # ── 记录 (AI 实际发言后调用) ──────────────────────

    def record_reply(self):
        self._rules.record_reply()

    # ── 状态查询 ──────────────────────────────────────

    @property
    def seconds_since_last_reply(self) -> float:
        return self._rules.seconds_since_last_reply

    @property
    def parser_state(self) -> ParserState:
        return self._parser.state

    @property
    def override_log(self) -> List[dict]:
        return self._rules.override_log

    def set_visual_available(self, available: bool):
        self._rules.set_visual_available(available)

    def set_watchdog_enabled(self, enabled: bool):
        self._watchdog_enabled = enabled

    def reset(self):
        self._parser.reset_state()
        self._rules.reset()

    # ── 内部: 上下文拼装 ──────────────────────────────

    def _build_context(
        self,
        messages: List[Dict[str, Any]],
        threads: List[Dict[str, Any]],
        visual: str,
        emotion: str,
        strategy: Dict[str, Any],
        wm: Dict[str, Any],
    ) -> str:
        parts = []

        # 新消息
        parts.append("【新消息】")
        for m in messages[-10:]:
            at = " [@你]" if m.get("mentioned_bot") else ""
            q = " [问题]" if m.get("is_question") else ""
            parts.append(
                f"[{m.get('user','?')}] {m.get('text') or ''}{at}{q}"
            )

        # 线程
        if threads:
            parts.append(f"\n【活跃线程】(共{len(threads)}个)")
            for t in threads[:5]:
                parts.append(
                    f"  #{t.get('id','?')} pri={t.get('priority',0):.1f} "
                    f"[{','.join(t.get('participants',[]))}] "
                    f"{t.get('topic_label','')}"
                )

        # 画面
        parts.append(f"\n【画面】{visual or '无视觉输入'}")

        # 情绪
        parts.append(f"【情绪】{emotion or '基线'}")

        # 策略
        phase = strategy.get('current_phase', '?')
        bias = strategy.get('speak_frequency_bias', 0)
        parts.append(f"【策略】phase={phase} bias={bias}")

        # 工作记忆
        secs = wm.get('seconds_since_last_reply', 0)
        parts.append(f"【距上次发言】{secs:.0f}秒" if secs > 0 else "【距上次发言】首次")

        parts.append("\n现在，请输出你的决策 Token：")
        return "\n".join(parts)
