"""STS2 gameplay controller for the Bilibili live adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol

import asyncio
import contextlib
import time
from uuid import uuid4

from .config import LiveAdapterSettings
from .constants import GATEWAY_NAME
from .message_codec import build_sts2_message_dict, sanitize_model_reserved_tokens


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


class _Sts2MCPProtocol(Protocol):
    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    async def health_check(self) -> dict[str, Any]:
        ...

    async def get_game_state(self) -> dict[str, Any]:
        ...

    async def get_available_actions(self) -> list[dict[str, Any]]:
        ...

    async def wait_until_actionable(self, *, timeout_seconds: float) -> dict[str, Any]:
        ...

    async def act(
        self,
        *,
        action: str,
        card_index: int | None = None,
        target_index: int | None = None,
        option_index: int | None = None,
    ) -> dict[str, Any]:
        ...


class _DecisionClientProtocol(Protocol):
    async def decide(
        self,
        *,
        state: Mapping[str, Any],
        available_actions: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> "STS2Decision":
        ...


_OPTION_INDEX_ACTIONS = frozenset(
    {
        "buy_card",
        "buy_potion",
        "buy_relic",
        "choose_event_option",
        "choose_map_node",
        "choose_rest_option",
        "choose_reward_card",
        "choose_timeline_epoch",
        "choose_treasure_relic",
        "claim_reward",
        "discard_potion",
        "select_character",
        "select_deck_card",
        "use_potion",
    }
)

_NO_INDEX_ACTIONS = frozenset(
    {
        "abandon_run",
        "close_main_menu_submenu",
        "close_shop_inventory",
        "collect_rewards_and_proceed",
        "confirm_modal",
        "confirm_selection",
        "confirm_timeline_overlay",
        "continue_run",
        "decrease_ascension",
        "dismiss_modal",
        "embark",
        "end_turn",
        "increase_ascension",
        "open_character_select",
        "open_chest",
        "open_shop_inventory",
        "open_timeline",
        "proceed",
        "remove_card_at_shop",
        "return_to_main_menu",
        "skip_reward_cards",
        "unready",
    }
)


@dataclass(frozen=True)
class STS2Decision:
    """One STS2 action selected by the decision LLM."""

    decision_id: str
    action: str
    reason: str
    narration: str
    card_index: int | None = None
    target_index: int | None = None
    option_index: int | None = None
    expected_result: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def action_kwargs(self) -> dict[str, Any]:
        action = str(self.action or "").strip().lower()
        kwargs: dict[str, Any] = {"action": action}
        if action == "play_card":
            card_index = self.card_index if self.card_index is not None else self.option_index
            if card_index is not None:
                kwargs["card_index"] = int(card_index)
        elif action in _OPTION_INDEX_ACTIONS:
            option_index = self.option_index if self.option_index is not None else self.card_index
            if option_index is not None:
                kwargs["option_index"] = int(option_index)
        elif action not in _NO_INDEX_ACTIONS:
            if self.card_index is not None:
                kwargs["card_index"] = int(self.card_index)
            if self.option_index is not None:
                kwargs["option_index"] = int(self.option_index)
        if self.target_index is not None:
            kwargs["target_index"] = int(self.target_index)
        return kwargs


class STS2Controller:
    """Owns the STS2 run loop and the speech-start action gate."""

    def __init__(
        self,
        *,
        gateway: _GatewayProtocol,
        settings: LiveAdapterSettings,
        mcp_client: _Sts2MCPProtocol,
        decision_client: _DecisionClientProtocol | None,
        logger: Any = None,
    ) -> None:
        self.gateway = gateway
        self.settings = settings
        self.mcp_client = mcp_client
        self.decision_client = decision_client
        self.logger = logger
        self._active = False
        self._run_task: asyncio.Task[None] | None = None
        self._pending_decision: STS2Decision | None = None
        self._pending_future: asyncio.Future[dict[str, Any]] | None = None
        self._pending_executing = False
        self._history: list[dict[str, Any]] = []
        self._live_context: list[dict[str, Any]] = []
        self._pending_live_replies: list[dict[str, Any]] = []

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def has_pending_decision(self) -> bool:
        return self._pending_decision is not None

    @property
    def pending_decision_id(self) -> str:
        decision = self._pending_decision
        return decision.decision_id if decision is not None else ""

    async def start_from_command(self, event: dict[str, Any]) -> bool:
        self._log_info(f"STS2 start command received: user_id={event.get('user_id')} text={event.get('text')!r}")
        if not self.settings.sts2.enabled:
            await self._route_text("STS2 功能未启用，已忽略启动命令。", event_type="sts2_status")
            return False
        if self._active:
            await self._route_text("STS2 已经在运行中，我会继续当前这局。", event_type="sts2_status")
            return True
        self._active = True
        self._history.clear()
        self._live_context.clear()
        self._pending_live_replies.clear()
        await self._route_text(
            "收到管理员指令，准备开始游玩杀戮尖塔2。先确认游戏和 MCP 服务状态。",
            event_type="sts2_status",
            payload={"command_event": dict(event)},
        )
        self._run_task = asyncio.create_task(self._run_loop(), name="bilibili_live.sts2_controller")
        return True

    async def stop_from_command(self, event: dict[str, Any]) -> bool:
        self._log_info(f"STS2 stop command received: user_id={event.get('user_id')} text={event.get('text')!r}")
        del event
        await self.stop(reason="管理员停止了 STS2 游玩。")
        return True

    async def status_from_command(self, event: dict[str, Any]) -> bool:
        del event
        status = "运行中" if self._active else "未运行"
        pending = "，有一个动作正在等待语音同步" if self.has_pending_decision else ""
        await self._route_text(f"STS2 当前状态：{status}{pending}。", event_type="sts2_status")
        return True

    async def stop(self, *, reason: str = "") -> None:
        self._log_info(f"Stopping STS2 controller: reason={reason!r}")
        was_active = self._active
        self._active = False
        task = self._run_task
        self._run_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._clear_pending()
        with contextlib.suppress(Exception):
            await self.mcp_client.stop()
        if was_active and reason:
            await self._route_text(reason, event_type="sts2_status")

    def queue_pending_decision(self, decision: STS2Decision) -> asyncio.Future[dict[str, Any]]:
        self._log_info(
            "Queued STS2 decision waiting for commentary audio: "
            f"decision_id={decision.decision_id} action={decision.action_kwargs()}"
        )
        self._pending_decision = decision
        self._pending_executing = False
        self._pending_future = asyncio.get_running_loop().create_future()
        return self._pending_future

    def build_audio_start_callback(self) -> Callable[[], None] | None:
        decision = self._pending_decision
        if decision is None:
            return None
        loop = asyncio.get_running_loop()
        fired = False

        def on_audio_start() -> None:
            nonlocal fired
            if fired:
                return
            fired = True
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self.notify_commentary_audio_started(decision.decision_id))
            )

        return on_audio_start

    def record_live_event_context(self, event: dict[str, Any]) -> None:
        context_item = _build_live_context_item(event)
        if not context_item:
            return
        self._live_context.append(context_item)
        self._pending_live_replies.append(context_item)
        max_items = max(4, int(self.settings.sts2.narration.max_recent_steps) * 2)
        self._live_context = self._live_context[-max_items:]
        self._pending_live_replies = self._pending_live_replies[-max_items:]

    async def notify_commentary_audio_started(self, decision_id: str) -> dict[str, Any]:
        decision = self._pending_decision
        future = self._pending_future
        if decision is None or future is None:
            return {"ok": False, "error": "no_pending_decision"}
        if decision.decision_id != str(decision_id or "").strip():
            return {"ok": False, "error": "decision_id_mismatch"}
        if self._pending_executing:
            return await future
        self._pending_executing = True
        try:
            self._log_info(
                "Commentary audio started; executing STS2 action: "
                f"decision_id={decision.decision_id} action={decision.action_kwargs()}"
            )
            result = await self.mcp_client.act(**decision.action_kwargs())
            with contextlib.suppress(Exception):
                result = {**result, "state_after": await self.mcp_client.get_game_state()}
            if not future.done():
                future.set_result(result)
            self._record_history(decision=decision, result=result)
            self._log_info(f"STS2 action executed: decision_id={decision.decision_id} result={result}")
            return result
        except Exception as exc:
            payload = {"ok": False, "error": str(exc), "action": decision.action_kwargs()}
            self._log_exception(
                "STS2 action failed after commentary audio started: "
                f"decision_id={decision.decision_id} action={decision.action_kwargs()}"
            )
            if not future.done():
                future.set_result(payload)
            return payload
        finally:
            self._clear_pending()

    async def _run_loop(self) -> None:
        try:
            self._log_info("STS2 controller loop starting")
            await self.mcp_client.start()
            health = await self.mcp_client.health_check()
            self._log_info(f"STS2 MCP health check result: {health}")
            await self._route_text(
                "STS2 MCP 已连接，开始读取当前局面并准备行动。",
                event_type="sts2_status",
                payload={"health": health},
            )
            while self._active:
                actionable = await self.mcp_client.wait_until_actionable(
                    timeout_seconds=self.settings.sts2.mcp.wait_actionable_timeout_sec
                )
                state = self._extract_state(actionable) or await self.mcp_client.get_game_state()
                available_actions = self._extract_actions(actionable) or await self.mcp_client.get_available_actions()
                self._log_info(
                    "STS2 actionable state loaded: "
                    f"state_keys={list(state)[:20]} available_actions={len(available_actions)}"
                )
                if not available_actions:
                    await asyncio.sleep(1.0)
                    continue
                if self.decision_client is None:
                    await self._route_text("STS2 决策模型未初始化，游玩已暂停。", event_type="sts2_error")
                    self._active = False
                    break
                decision_state = self._state_with_live_context(state)
                pending_live_reply_keys = _live_reply_keys(decision_state.get("pending_live_replies", []))
                decision = await self.decision_client.decide(
                    state=decision_state,
                    available_actions=available_actions,
                    history=list(self._history),
                )
                self._log_info(
                    "STS2 decision received: "
                    f"decision_id={decision.decision_id} action={decision.action_kwargs()} reason={decision.reason!r}"
                )
                if not self._is_available_action(decision.action, available_actions):
                    await self._route_text(
                        f"STS2 决策动作 {decision.action!r} 不在当前可用动作中，已跳过并重新读取状态。",
                        event_type="sts2_error",
                        payload={"decision": decision.raw, "available_actions": available_actions},
                    )
                    continue
                self._remove_pending_live_replies(pending_live_reply_keys)
                pending = self.queue_pending_decision(decision)
                await self._route_decision(decision, state=decision_state, available_actions=available_actions)
                result = await self._wait_for_pending_result(pending, decision)
                await self._route_result(decision, result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._active = False
            self._log_exception(f"STS2 controller stopped after error: {exc}")
            await self._route_text(f"STS2 游玩遇到错误，已暂停：{exc}", event_type="sts2_error")
        finally:
            self._log_info("STS2 controller loop stopped")
            self._active = False
            self._clear_pending()
            with contextlib.suppress(Exception):
                await self.mcp_client.stop()

    async def _wait_for_pending_result(
        self,
        pending: asyncio.Future[dict[str, Any]],
        decision: STS2Decision,
    ) -> dict[str, Any]:
        if not self.settings.sts2.narration.action_on_audio_start:
            return await self.notify_commentary_audio_started(decision.decision_id)
        return await pending

    async def _route_decision(
        self,
        decision: STS2Decision,
        *,
        state: Mapping[str, Any],
        available_actions: list[dict[str, Any]],
    ) -> None:
        action_text = _format_action(decision.action_kwargs())
        pending_live_replies = _coerce_live_reply_list(state.get("pending_live_replies"))
        pending_live_reply_text = _format_pending_live_replies(pending_live_replies)
        text = (
            "【杀戮尖塔2】请用第一人称自然直播解说，把游戏决策和弹幕回应融合成一段，"
            "不要提到外部模型、插件或 JSON 字段。\n"
            "{sts2决策}\n"
            f"我准备执行：{action_text}。\n"
            f"决策理由：{decision.reason}。\n"
            f"解说要点：{decision.narration}\n"
            "{在做出决策的同时直播间待回复的弹幕列表}\n"
            f"{pending_live_reply_text}\n"
            "优先回应 SC、舰长和礼物；普通弹幕择要回应。"
        )
        await self._route_text(
            text,
            event_type="sts2_decision",
            decision_id=decision.decision_id,
            payload={
                "decision": decision.raw or decision.action_kwargs(),
                "state": dict(state),
                "available_actions": available_actions,
                "pending_live_replies": pending_live_replies,
            },
        )

    async def _route_result(self, decision: STS2Decision, result: Mapping[str, Any]) -> None:
        ok = bool(result.get("ok", True)) and not result.get("error")
        action_text = _format_action(decision.action_kwargs())
        if ok:
            text = f"【杀戮尖塔2】刚才的操作 {action_text} 已执行完成。请根据新的游戏反馈继续自然解说。"
        else:
            text = f"【杀戮尖塔2】刚才的操作 {action_text} 没有成功：{result.get('error', '未知错误')}。请说明并重新判断。"
        await self._route_text(
            text,
            event_type="sts2_result",
            payload={"decision_id": decision.decision_id, "result": dict(result)},
        )

    async def _route_text(
        self,
        text: str,
        *,
        event_type: str,
        decision_id: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> bool:
        message = build_sts2_message_dict(
            self.settings,
            text=text,
            event_type=event_type,
            decision_id=decision_id,
            payload=payload or {},
        )
        message_id = str(message.get("message_id") or f"sts2-{uuid4().hex}")
        route_metadata = {
            "source": "sts2",
            "room_id": self.settings.bilibili.room_id,
            "selection_reason": "sts2_priority",
            "selection_score": 9999.0,
            "sts2_priority": True,
        }
        return await self.gateway.route_message(
            GATEWAY_NAME,
            message,
            route_metadata=route_metadata,
            external_message_id=message_id,
            dedupe_key=message_id,
        )

    def _record_history(self, *, decision: STS2Decision, result: Mapping[str, Any]) -> None:
        self._history.append(
            {
                "at": time.time(),
                "decision": decision.raw or decision.action_kwargs(),
                "reason": decision.reason,
                "result": dict(result),
            }
        )
        max_items = max(1, int(self.settings.sts2.narration.max_recent_steps))
        self._history = self._history[-max_items:]

    def _state_with_live_context(self, state: Mapping[str, Any]) -> dict[str, Any]:
        enriched = dict(state)
        if self._live_context:
            enriched["live_chat_context"] = [dict(item) for item in self._live_context]
        if self._pending_live_replies:
            enriched["pending_live_replies"] = _prioritize_live_replies(self._pending_live_replies)
        return enriched

    def _remove_pending_live_replies(self, handled_keys: set[str]) -> None:
        if not handled_keys:
            return
        self._pending_live_replies = [
            item for item in self._pending_live_replies if _live_reply_key(item) not in handled_keys
        ]

    def _clear_pending(self) -> None:
        self._pending_decision = None
        self._pending_future = None
        self._pending_executing = False

    def _log_warning(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warning(message)

    def _log_info(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger.info(message)
            except AttributeError:
                pass

    def _log_exception(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger.exception(message)
                return
            except AttributeError:
                pass
            self._log_warning(message)

    @staticmethod
    def _extract_state(actionable: Mapping[str, Any]) -> dict[str, Any]:
        state = actionable.get("state")
        return dict(state) if isinstance(state, Mapping) else {}

    @staticmethod
    def _extract_actions(actionable: Mapping[str, Any]) -> list[dict[str, Any]]:
        actions = actionable.get("actions")
        if not isinstance(actions, list):
            return []
        return [dict(item) for item in actions if isinstance(item, Mapping)]

    @staticmethod
    def _is_available_action(action: str, available_actions: list[dict[str, Any]]) -> bool:
        normalized = str(action or "").strip().lower()
        if not normalized:
            return False
        return any(_action_name(item) == normalized for item in available_actions)


def _action_name(action: Mapping[str, Any]) -> str:
    for key in ("action", "name", "id"):
        value = str(action.get(key) or "").strip().lower()
        if value:
            return value
    return ""

def _format_action(action_kwargs: Mapping[str, Any]) -> str:
    parts = [str(action_kwargs.get("action") or "").strip()]
    for key in ("card_index", "target_index", "option_index"):
        if key in action_kwargs:
            parts.append(f"{key}={action_kwargs[key]}")
    return ", ".join(part for part in parts if part)


def _coerce_live_reply_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _prioritize_live_replies(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = list(enumerate(items))
    indexed.sort(key=lambda pair: (_live_reply_priority_rank(pair[1]), pair[0]))
    return [dict(item) for _, item in indexed]


def _live_reply_priority_rank(item: Mapping[str, Any]) -> int:
    priority = str(item.get("priority") or "").strip()
    event_type = str(item.get("type") or "").strip()
    if priority == "super_chat" or event_type == "super_chat":
        return 0
    if priority in {"guard", "gift"} or event_type in {"guard", "gift"}:
        return 1
    return 2


def _format_pending_live_replies(items: list[dict[str, Any]]) -> str:
    if not items:
        return "（暂无待回复弹幕）"
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        event_type = str(item.get("type") or "danmaku").strip() or "danmaku"
        username = str(item.get("username") or item.get("user_id") or "anonymous").strip() or "anonymous"
        text = str(item.get("text") or item.get("summary") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if summary and summary != text:
            text = f"{text}（{summary}）"
        if not text:
            continue
        lines.append(f"{index}. [{event_type}] {username}: {text}")
    return "\n".join(lines) if lines else "（暂无待回复弹幕）"


def _live_reply_keys(value: Any) -> set[str]:
    return {_live_reply_key(item) for item in _coerce_live_reply_list(value)}


def _live_reply_key(item: Mapping[str, Any]) -> str:
    event_id = str(item.get("event_id") or "").strip()
    if event_id:
        return f"id:{event_id}"
    user_id = str(item.get("user_id") or "").strip()
    text = str(item.get("text") or item.get("summary") or "").strip()
    return f"fallback:{user_id}:{text}"


def _build_live_context_item(event: Mapping[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("type") or "").strip()
    text = sanitize_model_reserved_tokens(str(event.get("text") or ""))
    summary = sanitize_model_reserved_tokens(str(event.get("summary") or text))
    if not text and not summary:
        return {}
    priority = "super_chat" if event_type == "super_chat" else "normal"
    if event_type in {"gift", "guard"}:
        priority = event_type
    return {
        "event_id": str(event.get("event_id") or "").strip(),
        "type": event_type,
        "text": text or summary,
        "summary": summary,
        "username": str(event.get("username") or "").strip(),
        "user_id": str(event.get("user_id") or "").strip(),
        "priority": priority,
    }
