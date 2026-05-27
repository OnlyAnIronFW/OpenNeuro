"""Live danmaku selection before injecting messages into MaiBot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import json
import time

from .config import InteractionConfig


@dataclass(frozen=True)
class PlannerSelection:
    """A selected live event and its reason."""

    event: dict[str, Any]
    reason: str
    score: float


class _MessageCapabilityProtocol(Protocol):
    async def count_new(self, chat_id: str, since: str) -> Any:
        ...


class LiveInteractionPlanner:
    """Select high-value live events for MaiBot interaction."""

    def __init__(
        self,
        config: InteractionConfig,
        llm: Any = None,
        logger: Any = None,
        *,
        message_capability: _MessageCapabilityProtocol | None = None,
        chat_id: str = "",
    ) -> None:
        self.config = config
        self.llm = llm
        self.logger = logger
        self.message_capability = message_capability
        self.chat_id = str(chat_id or "").strip()
        self._last_inject_at = 0.0
        self._recent_injections: list[float] = []
        self._pending_count_anchor_at = time.time()

    async def select(
        self,
        events: list[Mapping[str, Any]],
        *,
        ai_speaking: bool = False,
    ) -> list[PlannerSelection]:
        """Select events from a live window."""

        if not self.config.enabled or not events:
            return []
        now = time.time()
        if await self._should_route_all_events(now):
            selected = [self._force_all_selection(dict(event), ai_speaking=ai_speaking) for event in events]
            if selected:
                self._record_injections(now, len(selected))
                self._log_info(
                    "Live interaction planner routed all buffered events: "
                    f"pending_count<={self.config.route_all_when_pending_leq} selected={len(selected)}"
                )
            return selected
        if not self._can_inject(now) and not _has_high_priority(events):
            return []
        scored = [self._score_event(dict(event), ai_speaking=ai_speaking) for event in events]
        scored = [item for item in scored if item.score > 0]
        if not scored:
            return []
        selected = await self._try_llm_select(scored) if self.config.llm_enabled else []
        if not selected:
            selected = sorted(scored, key=lambda item: item.score, reverse=True)[: self.config.max_selected_per_window]
        selected = selected[: self.config.max_selected_per_window]
        if selected:
            self._record_injections(now, len(selected))
        return selected

    async def _should_route_all_events(self, now: float) -> bool:
        del now
        if self.config.route_all_when_pending_leq <= 0:
            return False
        if self.message_capability is None or not self.chat_id:
            return False
        try:
            pending_count = await self.message_capability.count_new(
                chat_id=self.chat_id,
                since=str(self._pending_count_anchor_at),
            )
        except Exception as exc:
            self._log_warning(f"Live interaction planner pending-count query failed: {exc}")
            return False
        try:
            normalized_count = int(pending_count)
        except (TypeError, ValueError):
            return False
        if normalized_count <= self.config.route_all_when_pending_leq:
            return True
        return False

    def _force_all_selection(self, event: dict[str, Any], *, ai_speaking: bool) -> PlannerSelection:
        scored = self._score_event(event, ai_speaking=ai_speaking)
        reason_parts = [part for part in str(scored.reason or "").split(",") if part]
        if "small_queue_all" not in reason_parts:
            reason_parts.append("small_queue_all")
        return PlannerSelection(
            event=scored.event,
            reason=",".join(reason_parts) or "small_queue_all",
            score=max(scored.score, 1.0),
        )

    def _score_event(self, event: dict[str, Any], *, ai_speaking: bool) -> PlannerSelection:
        text = str(event.get("text") or event.get("summary") or "").strip()
        event_type = str(event.get("type") or "").strip()
        lowered_text = text.lower()
        score = 0.0
        reasons: list[str] = []
        if text:
            score += 0.35
            reasons.append("baseline")
        if event_type in {"super_chat", "gift", "guard"}:
            score += 6.0
            reasons.append(event_type)
        if any(name.lower() in lowered_text for name in self.config.bot_names):
            score += 4.0
            reasons.append("bot_name")
        if any(keyword.lower() in lowered_text for keyword in self.config.keywords):
            score += 2.5
            reasons.append("keyword")
        if any(mark in text for mark in "?!？！吗呢怎么为什么"):
            score += 1.8
            reasons.append("question")
        if any(mark in text for mark in "哈哈草笑哭绝绷乐"):
            score += 1.3
            reasons.append("funny")
        if len(text) >= 8:
            score += 0.7
        if ai_speaking and event_type not in {"super_chat", "gift", "guard"}:
            score *= self.config.speaking_slowdown_factor
            reasons.append("ai_speaking_slowdown")
        return PlannerSelection(event=event, reason=",".join(reasons) or "score", score=score)

    async def _try_llm_select(self, scored: list[PlannerSelection]) -> list[PlannerSelection]:
        if self.llm is None or len(scored) < 4:
            return []
        candidates = sorted(scored, key=lambda item: item.score, reverse=True)[: self.config.max_batch_size]
        prompt = self._build_llm_prompt(candidates)
        try:
            response = await self.llm.generate(prompt=prompt, temperature=0.1, max_tokens=256)
        except Exception as exc:
            self._log_warning(f"Live interaction planner LLM failed: {exc}")
            return []
        raw_text = _extract_llm_text(response)
        try:
            payload = json.loads(raw_text)
        except Exception:
            return []
        selected_ids = []
        if isinstance(payload, Mapping) and isinstance(payload.get("selected"), list):
            for item in payload["selected"]:
                if isinstance(item, Mapping):
                    selected_ids.append(str(item.get("event_id") or "").strip())
                else:
                    selected_ids.append(str(item).strip())
        if not selected_ids:
            return []
        by_id = {str(item.event.get("event_id") or ""): item for item in candidates}
        return [by_id[event_id] for event_id in selected_ids if event_id in by_id]

    def _build_llm_prompt(self, candidates: list[PlannerSelection]) -> str:
        lines = [
            "Pick 0-2 Bilibili live chat messages that are most worth responding to on stream.",
            "Prefer direct questions, funny remarks, super chats, gifts, or messages with strong show value.",
            'Return strict JSON only: {"selected":[{"event_id":"...","reason":"..."}]}',
            "Candidates:",
        ]
        for item in candidates:
            lines.append(
                f"- event_id={item.event.get('event_id')} user={item.event.get('username')} "
                f"type={item.event.get('type')} score={item.score:.2f} text={item.event.get('text')}"
            )
        return "\n".join(lines)

    def _can_inject(self, now: float) -> bool:
        if now - self._last_inject_at < self.config.min_inject_interval_sec:
            return False
        one_minute_ago = now - 60.0
        self._recent_injections = [stamp for stamp in self._recent_injections if stamp >= one_minute_ago]
        return len(self._recent_injections) < self.config.max_injections_per_minute

    def _record_injections(self, now: float, count: int) -> None:
        self._last_inject_at = now
        self._pending_count_anchor_at = now
        self._recent_injections.extend([now] * count)

    def _log_warning(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warning(message)

    def _log_info(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)


def _has_high_priority(events: list[Mapping[str, Any]]) -> bool:
    return any(str(event.get("type") or "") in {"super_chat", "gift", "guard"} for event in events)


def _extract_llm_text(response: Any) -> str:
    if isinstance(response, Mapping):
        return str(response.get("response") or response.get("text") or "")
    return str(response or "")
