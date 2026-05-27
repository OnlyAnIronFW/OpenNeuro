"""Inbound Bilibili live event router."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

import asyncio
import contextlib
import time
from uuid import uuid4

from .config import LiveAdapterSettings
from .constants import GATEWAY_NAME
from .interaction_planner import LiveInteractionPlanner
from .message_codec import build_message_dict, sanitize_model_reserved_tokens


class _GatewayProtocol(Protocol):
    async def route_message(
        self,
        gateway_name: str,
        message: dict[str, Any],
        *,
        route_metadata: dict[str, Any] | None = None,
        external_message_id: str = "",
        dedupe_key: str = "",
    ) -> bool:
        ...


class _JsonBridgeProtocol(Protocol):
    async def send(self, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        ...


class _Live2DControllerProtocol(Protocol):
    @property
    def is_speaking(self) -> bool:
        ...

    @property
    def bridge(self) -> Any:
        ...


class _STS2ControllerProtocol(Protocol):
    @property
    def is_active(self) -> bool:
        ...

    @property
    def has_pending_decision(self) -> bool:
        ...

    def record_live_event_context(self, event: dict[str, Any]) -> None:
        ...

    async def start_from_command(self, event: dict[str, Any]) -> bool:
        ...

    async def stop_from_command(self, event: dict[str, Any]) -> bool:
        ...

    async def status_from_command(self, event: dict[str, Any]) -> bool:
        ...


class LiveEventRouter:
    """Filter, plan, and route Bilibili live events."""

    def __init__(
        self,
        *,
        gateway: _GatewayProtocol,
        settings: LiveAdapterSettings,
        planner: LiveInteractionPlanner,
        live2d_controller: _Live2DControllerProtocol | None = None,
        game_bridge: _JsonBridgeProtocol | None = None,
        sts2_controller: _STS2ControllerProtocol | None = None,
        logger: Any = None,
    ) -> None:
        self.gateway = gateway
        self.settings = settings
        self.planner = planner
        self.live2d_controller = live2d_controller
        self.game_bridge = game_bridge
        self.sts2_controller = sts2_controller
        self.logger = logger
        self._buffer: list[dict[str, Any]] = []
        self._seen_ids: dict[str, float] = {}
        self._flush_task: asyncio.Task[None] | None = None
        self._idle_topic_task: asyncio.Task[None] | None = None
        self._recent_live_records: list[dict[str, Any]] = []
        self._recent_idle_topics: list[str] = []

    async def handle_event(self, event: Mapping[str, Any]) -> None:
        """Handle one normalized Bilibili live event."""

        normalized_event = self._sanitize_inbound_event(dict(event))
        self._log_inbound_event(normalized_event)
        if self._is_duplicate_event(normalized_event):
            return
        if self._counts_as_live_activity(normalized_event):
            self._schedule_idle_topic(restart=True)
        if await self._handle_sts2_command(normalized_event):
            return
        if not self._passes_filters(normalized_event):
            return
        self._record_recent_live_event(normalized_event)
        self._record_sts2_live_context(normalized_event)
        await self._forward_environment_event(normalized_event)
        if self._should_collect_for_sts2_decision(normalized_event):
            self._log_sts2_collected_event(normalized_event)
            return
        self._buffer.append(normalized_event)
        self._buffer = self._buffer[-self.settings.interaction.max_batch_size :]
        self._schedule_flush()

    async def flush_window(self) -> list[dict[str, Any]]:
        """Flush the current selection window and route selected events."""

        events = self._buffer
        self._buffer = []
        if not events:
            self._log_planner_flush(buffered_count=0, selected_count=0, routed_count=0)
            return []
        routable_events, deferred_events = self._partition_events_for_sts2(events)
        if not routable_events:
            self._buffer = (deferred_events + self._buffer)[-self.settings.interaction.max_batch_size :]
            self._log_planner_flush(buffered_count=len(events), selected_count=0, routed_count=0)
            return []
        selected = await self.planner.select(routable_events, ai_speaking=self._is_ai_speaking())
        routed: list[dict[str, Any]] = []
        for item in selected:
            message = build_message_dict(item.event, self.settings, reason=item.reason)
            route_metadata = {
                "source": "bilibili_live",
                "room_id": self.settings.bilibili.room_id,
                "selection_reason": item.reason,
                "selection_score": item.score,
            }
            accepted = await self.gateway.route_message(
                GATEWAY_NAME,
                message,
                route_metadata=route_metadata,
                external_message_id=str(item.event.get("event_id") or ""),
                dedupe_key=str(item.event.get("event_id") or ""),
            )
            if accepted:
                routed.append(message)
        if deferred_events:
            self._buffer = (deferred_events + self._buffer)[-self.settings.interaction.max_batch_size :]
        self._log_planner_flush(
            buffered_count=len(events),
            selected_count=len(selected),
            routed_count=len(routed),
        )
        return routed

    def start_idle_topic_watch(self) -> None:
        """Start the idle-topic timer for a connected live room."""

        self._schedule_idle_topic(restart=True)

    def stop_idle_topic_watch(self) -> None:
        """Stop the idle-topic timer without clearing other route state."""

        if self._idle_topic_task is not None:
            self._idle_topic_task.cancel()
        self._idle_topic_task = None

    def reset(self) -> None:
        """Clear route buffers and cancel pending timers."""

        self._buffer.clear()
        self._seen_ids.clear()
        self._recent_live_records.clear()
        self._recent_idle_topics.clear()
        if self._flush_task is not None:
            self._flush_task.cancel()
        self._flush_task = None
        self.stop_idle_topic_watch()

    def record_idle_topic_reply(self, text: str) -> None:
        """Remember a bot-initiated idle topic so the next prompt can avoid repetition."""

        normalized_text = _normalize_context_text(text, max_length=180)
        if not normalized_text:
            return
        if self._recent_idle_topics and self._recent_idle_topics[-1] == normalized_text:
            return
        self._recent_idle_topics.append(normalized_text)
        limit = max(1, int(self.settings.interaction.idle_topic_history_limit))
        self._recent_idle_topics = self._recent_idle_topics[-limit:]

    def _is_duplicate_event(self, event: Mapping[str, Any]) -> bool:
        event_id = str(event.get("event_id") or "").strip()
        if event_id:
            now = time.time()
            self._seen_ids = {key: stamp for key, stamp in self._seen_ids.items() if now - stamp < 120.0}
            if event_id in self._seen_ids:
                return True
            self._seen_ids[event_id] = now
        return False

    def _passes_filters(self, event: dict[str, Any]) -> bool:
        user_id = str(event.get("user_id") or "").strip()
        if user_id and user_id in self.settings.filters.ignored_user_ids:
            return False
        if not self.settings.bilibili.route_gifts_as_messages and str(event.get("type") or "") in {
            "gift",
            "guard",
            "super_chat",
        }:
            return False
        text = str(event.get("text") or event.get("summary") or "").strip()
        if len(text) < self.settings.filters.min_text_length:
            return False
        lowered_text = text.lower()
        return not any(blocked.lower() in lowered_text for blocked in self.settings.filters.blocked_words)

    @staticmethod
    def _counts_as_live_activity(event: Mapping[str, Any]) -> bool:
        event_type = str(event.get("type") or "").strip()
        if event_type not in {"danmaku", "super_chat", "gift", "guard"}:
            return False
        if event_type in {"gift", "guard"}:
            return True
        text = str(event.get("text") or event.get("summary") or "").strip()
        return bool(text)

    def _record_recent_live_event(self, event: Mapping[str, Any]) -> None:
        event_type = str(event.get("type") or "").strip()
        if event_type not in {"danmaku", "super_chat", "gift", "guard"}:
            return
        text = _normalize_context_text(str(event.get("text") or event.get("summary") or ""), max_length=140)
        if not text:
            return
        username = _normalize_context_text(str(event.get("username") or event.get("user_id") or "anonymous"), max_length=32)
        self._recent_live_records.append(
            {
                "type": event_type,
                "username": username or "anonymous",
                "text": text,
            }
        )
        limit = max(1, int(self.settings.interaction.idle_topic_context_limit))
        self._recent_live_records = self._recent_live_records[-limit:]

    @staticmethod
    def _sanitize_inbound_event(event: dict[str, Any]) -> dict[str, Any]:
        for key in ("text", "summary"):
            if key in event:
                event[key] = sanitize_model_reserved_tokens(str(event.get(key) or ""))
        return event

    def _record_sts2_live_context(self, event: dict[str, Any]) -> None:
        if not self.settings.sts2.enabled or self.sts2_controller is None:
            return
        if not self.sts2_controller.is_active:
            return
        recorder = getattr(self.sts2_controller, "record_live_event_context", None)
        if not callable(recorder):
            return
        with contextlib.suppress(Exception):
            recorder(dict(event))

    async def _forward_environment_event(self, event: Mapping[str, Any]) -> None:
        if self.settings.live2d.enabled and self.settings.live2d.forward_inbound_danmaku and self.live2d_controller:
            with contextlib.suppress(Exception):
                await self.live2d_controller.bridge.send_event(
                    {
                        "type": "live.danmaku",
                        "event": dict(event),
                    }
                )
        if self.settings.game.enabled and self.settings.game.forward_inbound_danmaku and self.game_bridge:
            with contextlib.suppress(Exception):
                await self.game_bridge.send("danmaku", {"event": dict(event)})

    def _schedule_flush(self) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            return
        self._flush_task = asyncio.create_task(self._delayed_flush(), name="bilibili_live.router_flush")

    def _schedule_idle_topic(self, *, restart: bool = False) -> None:
        if not self._idle_topic_enabled():
            self.stop_idle_topic_watch()
            return
        if restart:
            self.stop_idle_topic_watch()
        elif self._idle_topic_task is not None and not self._idle_topic_task.done():
            return
        self._idle_topic_task = asyncio.create_task(
            self._idle_topic_loop(),
            name="bilibili_live.idle_topic",
        )

    async def _delayed_flush(self) -> None:
        await asyncio.sleep(max(0.05, float(self.settings.interaction.window_seconds)))
        try:
            await self.flush_window()
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning(f"Bilibili live event routing failed: {exc}")

    async def _idle_topic_loop(self) -> None:
        delay_sec = max(0.05, float(self.settings.interaction.idle_topic_after_sec))
        while True:
            await asyncio.sleep(delay_sec)
            if self._is_ai_speaking():
                continue
            try:
                await self._route_idle_topic()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning(f"Bilibili idle topic routing failed: {exc}")

    async def _route_idle_topic(self) -> bool:
        if self._should_defer_for_sts2():
            return False
        prompt = self._build_idle_topic_prompt()
        if not prompt:
            return False
        event_id = f"bilibili-idle-topic-{uuid4().hex}"
        event = {
            "event_id": event_id,
            "type": "idle_topic",
            "text": prompt,
            "summary": prompt,
            "user_id": "bilibili-live-idle",
            "username": "\u76f4\u64ad\u95f4",
            "timestamp": time.time(),
        }
        message = build_message_dict(event, self.settings, reason="idle_topic")
        route_metadata = {
            "source": "bilibili_live",
            "room_id": self.settings.bilibili.room_id,
            "selection_reason": "idle_topic",
            "selection_score": 0.0,
        }
        accepted = await self.gateway.route_message(
            GATEWAY_NAME,
            message,
            route_metadata=route_metadata,
            external_message_id=event_id,
            dedupe_key=event_id,
        )
        if accepted and self.logger is not None:
            self.logger.info(
                "Bilibili idle topic injected: "
                f"room_id={self.settings.bilibili.room_id} event_id={event_id}"
        )
        return bool(accepted)

    def _build_idle_topic_prompt(self) -> str:
        base_prompt = str(self.settings.interaction.idle_topic_prompt or "").strip()
        if not base_prompt:
            return ""
        if not self.settings.interaction.idle_topic_context_enabled:
            return base_prompt
        sections = [base_prompt]
        live_records = self._format_recent_live_records()
        if live_records:
            sections.append("\u6700\u8fd1\u76f4\u64ad\u95f4\u8bb0\u5f55\uff1a\n" + live_records)
        idle_topics = self._format_recent_idle_topics()
        if idle_topics:
            sections.append("\u524d\u51e0\u6b21\u4e3b\u52a8\u804a\u8fc7\u7684\u8bdd\u9898\uff1a\n" + idle_topics)
            sections.append("\u8bf7\u907f\u514d\u91cd\u590d\u4e0a\u9762\u5df2\u7ecf\u804a\u8fc7\u7684\u5185\u5bb9\u3002")
        return "\n\n".join(sections)

    def _format_recent_live_records(self) -> str:
        if not self._recent_live_records:
            return ""
        limit = max(1, int(self.settings.interaction.idle_topic_context_limit))
        lines = []
        for record in self._recent_live_records[-limit:]:
            username = str(record.get("username") or "anonymous")
            text = str(record.get("text") or "")
            if text:
                lines.append(f"- {username}: {text}")
        return "\n".join(lines)

    def _format_recent_idle_topics(self) -> str:
        if not self._recent_idle_topics:
            return ""
        limit = max(1, int(self.settings.interaction.idle_topic_history_limit))
        return "\n".join(f"- {topic}" for topic in self._recent_idle_topics[-limit:])

    async def _handle_sts2_command(self, event: dict[str, Any]) -> bool:
        if not self.settings.sts2.enabled:
            return False
        text = str(event.get("text") or event.get("summary") or "").strip()
        if not text.startswith("/"):
            return False
        user_id = str(event.get("user_id") or "").strip()
        is_admin = bool(user_id and user_id in self.settings.sts2.commands.admin_user_ids)
        if not is_admin:
            return bool(self.settings.sts2.commands.drop_non_admin_slash_commands)
        controller = self.sts2_controller
        if controller is None:
            if self.logger is not None:
                self.logger.warning("STS2 command ignored because controller is unavailable.")
            return text in {
                self.settings.sts2.commands.start_command,
                self.settings.sts2.commands.stop_command,
                self.settings.sts2.commands.status_command,
            }
        if text == self.settings.sts2.commands.start_command:
            await controller.start_from_command(event)
            return True
        if text == self.settings.sts2.commands.stop_command:
            await controller.stop_from_command(event)
            return True
        if text == self.settings.sts2.commands.status_command:
            await controller.status_from_command(event)
            return True
        return False

    def _idle_topic_enabled(self) -> bool:
        return bool(
            self.settings.interaction.enabled
            and self.settings.interaction.idle_topic_enabled
            and str(self.settings.interaction.idle_topic_prompt or "").strip()
        )

    def _is_ai_speaking(self) -> bool:
        return bool(self.live2d_controller is not None and self.live2d_controller.is_speaking)

    def _should_defer_for_sts2(self) -> bool:
        return bool(
            self.settings.sts2.enabled
            and self.settings.sts2.narration.priority_over_danmaku
            and self.sts2_controller is not None
            and self.sts2_controller.is_active
            and self._sts2_has_pending_decision()
        )

    def _should_collect_for_sts2_decision(self, event: Mapping[str, Any]) -> bool:
        return bool(
            self.settings.sts2.enabled
            and self.settings.sts2.narration.priority_over_danmaku
            and self.sts2_controller is not None
            and self.sts2_controller.is_active
            and _is_live_reply_event(event)
        )

    def _partition_events_for_sts2(self, events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not self._should_defer_for_sts2():
            return events, []
        routable = [event for event in events if _is_high_priority_live_event(event)]
        deferred = [event for event in events if not _is_high_priority_live_event(event)]
        return routable, deferred

    def _sts2_has_pending_decision(self) -> bool:
        if self.sts2_controller is None:
            return False
        return bool(getattr(self.sts2_controller, "has_pending_decision", False))

    def _log_inbound_event(self, event: Mapping[str, Any]) -> None:
        if self.logger is None:
            return
        event_type = str(event.get("type") or "").strip()
        if event_type != "danmaku":
            return
        username = str(event.get("username") or "anonymous").strip() or "anonymous"
        user_id = str(event.get("user_id") or "").strip()
        text = self._sanitize_log_text(str(event.get("text") or event.get("summary") or "").strip())
        event_id = str(event.get("event_id") or "").strip()
        sender = f"{username}({user_id})" if user_id else username
        self.logger.info(
            "Bilibili inbound danmaku: "
            f"room_id={self.settings.bilibili.room_id} user={sender} text={text!r} event_id={event_id or '-'}"
        )

    def _log_planner_flush(self, *, buffered_count: int, selected_count: int, routed_count: int) -> None:
        if self.logger is None:
            return
        self.logger.info(
            "Bilibili planner flush: "
            f"buffered={buffered_count} selected={selected_count} routed={routed_count}"
        )

    def _log_sts2_collected_event(self, event: Mapping[str, Any]) -> None:
        if self.logger is None:
            return
        event_type = str(event.get("type") or "").strip()
        username = str(event.get("username") or "anonymous").strip() or "anonymous"
        event_id = str(event.get("event_id") or "").strip()
        self.logger.info(
            "Bilibili live event collected for STS2 decision narration: "
            f"type={event_type} user={username} event_id={event_id or '-'}"
        )

    @staticmethod
    def _sanitize_log_text(text: str, *, max_length: int = 160) -> str:
        normalized = " ".join(str(text).split())
        if len(normalized) <= max_length:
            return normalized
        return normalized[: max_length - 3] + "..."


def _is_high_priority_live_event(event: Mapping[str, Any]) -> bool:
    return str(event.get("type") or "").strip() in {"super_chat", "gift", "guard"}


def _is_live_reply_event(event: Mapping[str, Any]) -> bool:
    return str(event.get("type") or "").strip() in {"danmaku", "super_chat", "gift", "guard"}


def _normalize_context_text(text: str, *, max_length: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max(0, max_length - 3)] + "..."
