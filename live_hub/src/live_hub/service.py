"""Standalone hub service that mirrors Bilibili events into a local web UI."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import asyncio
import contextlib
import json
import time
from uuid import uuid4

from aiohttp import WSMsgType, web

from plugins.bilibili_live_adapter.bilibili_transport import extract_history_events
from plugins.bilibili_live_adapter.bilibili_transport import BilibiliDanmakuTransport

from .config import ClientIdentityMapping, LiveHubSettings


_GENERIC_LOCAL_USER_IDS = {"", "0", "hub-local", "anonymous", "none", "null"}


def _normalize_public_text(event: Mapping[str, Any]) -> str:
    return str(event.get("summary") or event.get("text") or "").strip()


def _normalize_public_username(
    event: Mapping[str, Any], *, default: str = "anonymous"
) -> str:
    username = str(event.get("username") or "").strip()
    if username:
        return username
    user_id = str(event.get("user_id") or "").strip()
    return user_id or default


def _normalize_public_timestamp(value: Any) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return time.time()
    if normalized > 10_000_000_000:
        normalized /= 1000.0
    return normalized


def _normalize_client_reply_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    inner_payload = payload.get("payload")
    if isinstance(inner_payload, Mapping):
        normalized = dict(inner_payload)
        for key in (
            "client_id",
            "bot_name",
            "text",
            "room_id",
            "live_event_type",
            "route_scope",
        ):
            if key not in normalized and key in payload:
                normalized[key] = payload[key]
        return normalized
    return dict(payload)


def _normalize_client_presence_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    inner_payload = payload.get("payload")
    if isinstance(inner_payload, Mapping):
        normalized = dict(inner_payload)
        for key in ("client_id", "bot_name", "forward_user_id", "forward_username"):
            if key not in normalized and key in payload:
                normalized[key] = payload[key]
        return normalized
    return dict(payload)


def _normalize_client_speak_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    inner_payload = payload.get("payload")
    if isinstance(inner_payload, Mapping):
        normalized = dict(inner_payload)
        for key in (
            "client_id",
            "bot_name",
            "request_id",
            "text",
            "expected_duration_ms",
            "status",
        ):
            if key not in normalized and key in payload:
                normalized[key] = payload[key]
        return normalized
    return dict(payload)


def _resolve_local_injected_user_id(user_id: Any, *, username: str) -> str:
    normalized_username = str(username or "").strip() or "Hub Local"
    normalized_user_id = str(user_id or "").strip()
    if normalized_user_id.casefold() in _GENERIC_LOCAL_USER_IDS:
        normalized_user_id = ""
    return normalized_user_id or normalized_username or "hub-local"


def _preferred_local_inject_form_user_id(user_id: Any) -> str:
    normalized_user_id = str(user_id or "").strip()
    if normalized_user_id.casefold() in _GENERIC_LOCAL_USER_IDS:
        return ""
    return normalized_user_id


def _normalize_public_raw_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_public_raw_value(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_normalize_public_raw_value(item) for item in value]
    return str(value)


def build_local_injected_event(
    *,
    text: str,
    username: str,
    user_id: str,
    timestamp: float | None = None,
) -> dict[str, Any]:
    normalized_text = str(text or "").strip()
    normalized_username = str(username or "").strip() or "Hub Local"
    normalized_user_id = _resolve_local_injected_user_id(
        user_id, username=normalized_username
    )
    event_timestamp = time.time() if timestamp is None else float(timestamp)
    event_id = f"hub-local-{uuid4().hex}"
    return {
        "event_id": event_id,
        "type": "local_inject",
        "text": normalized_text,
        "summary": normalized_text,
        "user_id": normalized_user_id,
        "username": normalized_username,
        "timestamp": event_timestamp,
        "raw": {
            "source": "hub_local_input",
            "username": normalized_username,
            "user_id": normalized_user_id,
        },
    }


def build_client_reply_event(
    *,
    client_id: str,
    bot_name: str,
    text: str,
    forward_user_id: str = "",
    forward_username: str = "",
    timestamp: float | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_text = str(text or "").strip()
    normalized_client_id = str(client_id or "").strip() or "hub-client"
    normalized_bot_name = str(bot_name or "").strip() or normalized_client_id
    normalized_forward_user_id = (
        str(forward_user_id or "").strip() or normalized_client_id
    )
    normalized_forward_username = (
        str(forward_username or "").strip()
        or normalized_bot_name
        or normalized_forward_user_id
    )
    event_timestamp = time.time() if timestamp is None else float(timestamp)
    event_id = f"hub-client-reply-{uuid4().hex}"
    raw = {
        "source": "hub_client_reply",
        "client_id": normalized_client_id,
        "bot_name": normalized_bot_name,
        "forward_user_id": normalized_forward_user_id,
        "forward_username": normalized_forward_username,
    }
    if isinstance(metadata, Mapping):
        for key in ("room_id", "live_event_type", "route_scope"):
            value = str(metadata.get(key) or "").strip()
            if value:
                raw[key] = value
    return {
        "event_id": event_id,
        "type": "bot_reply",
        "text": normalized_text,
        "summary": normalized_text,
        "user_id": normalized_forward_user_id,
        "username": normalized_forward_username,
        "timestamp": event_timestamp,
        "raw": raw,
    }


@dataclass(slots=True)
class HubClientPresence:
    client_id: str
    bot_name: str
    forward_user_id: str
    forward_username: str
    connected_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)

    def to_public_payload(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "bot_name": self.bot_name,
            "forward_user_id": self.forward_user_id,
            "forward_username": self.forward_username,
            "connected_at": self.connected_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass(slots=True)
class HubSpeakingRequest:
    request_id: str
    client_id: str
    bot_name: str
    text: str
    expected_duration_ms: int = 0
    created_at: float = field(default_factory=time.time)
    granted_at: float = 0.0

    def to_public_payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "client_id": self.client_id,
            "bot_name": self.bot_name,
            "text": self.text,
            "expected_duration_ms": self.expected_duration_ms,
            "created_at": self.created_at,
            "granted_at": self.granted_at or None,
        }


@dataclass(slots=True)
class HubRuntimeState:
    title: str
    room_id: int
    recent_events_limit: int
    history_snapshot_limit: int
    recent_events: deque[dict[str, Any]] = field(default_factory=deque)
    sequence: int = 0
    total_events: int = 0
    total_danmaku_events: int = 0
    local_injected_events: int = 0
    client_reply_events: int = 0
    connected: bool = False
    connection_open_count: int = 0
    connection_close_count: int = 0
    started_at: float = field(default_factory=time.time)
    last_event_at: float = 0.0
    last_danmaku_at: float = 0.0
    last_connection_opened_at: float = 0.0
    last_connection_closed_at: float = 0.0
    active_clients: dict[str, HubClientPresence] = field(default_factory=dict)
    current_speaking_request: HubSpeakingRequest | None = None
    speaking_queue: deque[HubSpeakingRequest] = field(default_factory=deque)

    def record_public_event(
        self, event: Mapping[str, Any], *, origin: str
    ) -> dict[str, Any]:
        normalized_event_type = str(event.get("type") or "event").strip() or "event"
        timestamp = _normalize_public_timestamp(event.get("timestamp"))
        record = {
            "seq": self.sequence + 1,
            "event_id": str(event.get("event_id") or f"{origin}-{uuid4().hex}").strip(),
            "type": normalized_event_type,
            "origin": origin,
            "username": _normalize_public_username(event),
            "user_id": str(event.get("user_id") or "").strip(),
            "text": _normalize_public_text(event),
            "timestamp": timestamp,
            "display_time": datetime.fromtimestamp(timestamp).strftime("%H:%M:%S"),
            "received_at": time.time(),
            "raw": _normalize_public_raw_value(event),
        }
        self.sequence = int(record["seq"])
        self.total_events += 1
        self.last_event_at = float(record["received_at"])
        if normalized_event_type == "danmaku":
            self.total_danmaku_events += 1
            self.last_danmaku_at = float(record["received_at"])
        elif normalized_event_type == "local_inject":
            self.local_injected_events += 1
        elif normalized_event_type == "bot_reply":
            self.client_reply_events += 1
        self.recent_events.append(record)
        while len(self.recent_events) > self.recent_events_limit:
            self.recent_events.popleft()
        return record

    def record_connection_state(self, *, connected: bool) -> None:
        now = time.time()
        self.connected = connected
        if connected:
            self.connection_open_count += 1
            self.last_connection_opened_at = now
        else:
            self.connection_close_count += 1
            self.last_connection_closed_at = now

    def build_health_payload(self) -> dict[str, Any]:
        now = time.time()
        return {
            "title": self.title,
            "room_id": self.room_id,
            "connected": self.connected,
            "uptime_sec": max(0.0, now - self.started_at),
            "total_events": self.total_events,
            "total_danmaku_events": self.total_danmaku_events,
            "local_injected_events": self.local_injected_events,
            "client_reply_events": self.client_reply_events,
            "connection_open_count": self.connection_open_count,
            "connection_close_count": self.connection_close_count,
            "recent_event_count": len(self.recent_events),
            "last_event_at": self.last_event_at or None,
            "last_danmaku_at": self.last_danmaku_at or None,
            "last_connection_opened_at": self.last_connection_opened_at or None,
            "last_connection_closed_at": self.last_connection_closed_at or None,
            "active_client_count": len(self.active_clients),
            "speaking_pending_count": len(self.speaking_queue),
            "speaking_current_client_id": (
                self.current_speaking_request.client_id
                if self.current_speaking_request is not None
                else None
            ),
            "speaking_current_request_id": (
                self.current_speaking_request.request_id
                if self.current_speaking_request is not None
                else None
            ),
        }

    def build_history_snapshot(self) -> list[dict[str, Any]]:
        return list(self.recent_events)[-self.history_snapshot_limit :]

    def upsert_client_presence(
        self,
        *,
        client_id: str,
        bot_name: str,
        forward_user_id: str,
        forward_username: str,
    ) -> bool:
        normalized_client_id = str(client_id or "").strip()
        if not normalized_client_id:
            return False
        normalized_bot_name = str(bot_name or "").strip() or normalized_client_id
        normalized_forward_user_id = (
            str(forward_user_id or "").strip() or normalized_client_id
        )
        normalized_forward_username = (
            str(forward_username or "").strip()
            or normalized_bot_name
            or normalized_forward_user_id
        )
        current = self.active_clients.get(normalized_client_id)
        now = time.time()
        changed = current is None or any(
            (
                current.bot_name != normalized_bot_name,
                current.forward_user_id != normalized_forward_user_id,
                current.forward_username != normalized_forward_username,
            )
        )
        connected_at = current.connected_at if current is not None else now
        self.active_clients[normalized_client_id] = HubClientPresence(
            client_id=normalized_client_id,
            bot_name=normalized_bot_name,
            forward_user_id=normalized_forward_user_id,
            forward_username=normalized_forward_username,
            connected_at=connected_at,
            last_seen_at=now,
        )
        return changed

    def prune_stale_clients(self, *, ttl_sec: float) -> bool:
        ttl = max(1.0, float(ttl_sec or 1.0))
        now = time.time()
        stale_client_ids = [
            client_id
            for client_id, presence in self.active_clients.items()
            if now - float(presence.last_seen_at) > ttl
        ]
        if not stale_client_ids:
            return False
        for client_id in stale_client_ids:
            self.active_clients.pop(client_id, None)
        return True

    def build_participants_payload(self) -> list[dict[str, Any]]:
        participants = [
            presence.to_public_payload() for presence in self.active_clients.values()
        ]
        participants.sort(
            key=lambda item: (
                str(item.get("bot_name") or ""),
                str(item.get("client_id") or ""),
            )
        )
        return participants

    def build_speaking_payload(self, *, enabled: bool = True) -> dict[str, Any]:
        return {
            "enabled": bool(enabled),
            "current": (
                self.current_speaking_request.to_public_payload()
                if self.current_speaking_request is not None
                else None
            ),
            "queue": [item.to_public_payload() for item in self.speaking_queue],
            "pending_count": len(self.speaking_queue),
        }

    def _promote_next_speaking_request(self) -> None:
        if self.current_speaking_request is not None:
            return
        while self.speaking_queue:
            next_request = self.speaking_queue.popleft()
            next_request.granted_at = time.time()
            self.current_speaking_request = next_request
            return

    def request_speaking_turn(
        self,
        *,
        request_id: str,
        client_id: str,
        bot_name: str,
        text: str,
        expected_duration_ms: int = 0,
    ) -> dict[str, Any]:
        normalized_request_id = str(request_id or "").strip()
        normalized_client_id = str(client_id or "").strip()
        normalized_bot_name = str(bot_name or "").strip() or normalized_client_id
        normalized_text = str(text or "").strip()
        normalized_duration_ms = max(0, int(expected_duration_ms or 0))
        current = self.current_speaking_request
        if (
            current is not None
            and current.request_id == normalized_request_id
            and current.client_id == normalized_client_id
        ):
            return {"granted": True, "position": 0}
        for index, pending in enumerate(self.speaking_queue):
            if (
                pending.request_id == normalized_request_id
                and pending.client_id == normalized_client_id
            ):
                return {"granted": False, "position": index + 1}
        self.speaking_queue.append(
            HubSpeakingRequest(
                request_id=normalized_request_id,
                client_id=normalized_client_id,
                bot_name=normalized_bot_name,
                text=normalized_text,
                expected_duration_ms=normalized_duration_ms,
            )
        )
        if self.current_speaking_request is None:
            self._promote_next_speaking_request()
        current = self.current_speaking_request
        if (
            current is not None
            and current.request_id == normalized_request_id
            and current.client_id == normalized_client_id
        ):
            return {"granted": True, "position": 0}
        for index, pending in enumerate(self.speaking_queue):
            if (
                pending.request_id == normalized_request_id
                and pending.client_id == normalized_client_id
            ):
                return {"granted": False, "position": index + 1}
        return {"granted": False, "position": max(1, len(self.speaking_queue))}

    def complete_speaking_turn(self, *, request_id: str, client_id: str) -> bool:
        normalized_request_id = str(request_id or "").strip()
        normalized_client_id = str(client_id or "").strip()
        current = self.current_speaking_request
        if (
            current is not None
            and current.request_id == normalized_request_id
            and current.client_id == normalized_client_id
        ):
            self.current_speaking_request = None
            self._promote_next_speaking_request()
            return True
        remaining_queue = deque(
            item
            for item in self.speaking_queue
            if not (
                item.request_id == normalized_request_id
                and item.client_id == normalized_client_id
            )
        )
        if len(remaining_queue) != len(self.speaking_queue):
            self.speaking_queue = remaining_queue
            if self.current_speaking_request is None:
                self._promote_next_speaking_request()
            return True
        return False

    def prune_stale_speaking_requests(
        self,
        *,
        active_client_ids: set[str],
        stale_speaker_timeout_sec: float,
    ) -> bool:
        normalized_active_client_ids = {
            str(value or "").strip()
            for value in active_client_ids
            if str(value or "").strip()
        }
        changed = False
        current = self.current_speaking_request
        now = time.time()
        stale_timeout_sec = max(1.0, float(stale_speaker_timeout_sec or 1.0))
        if current is not None:
            current_is_stale = (
                current.client_id not in normalized_active_client_ids
                or (
                    current.granted_at > 0
                    and now - float(current.granted_at) > stale_timeout_sec
                )
            )
            if current_is_stale:
                self.current_speaking_request = None
                changed = True
        filtered_queue = deque(
            item
            for item in self.speaking_queue
            if item.client_id in normalized_active_client_ids
        )
        if len(filtered_queue) != len(self.speaking_queue):
            self.speaking_queue = filtered_queue
            changed = True
        if self.current_speaking_request is None:
            queue_before_promote = len(self.speaking_queue)
            self._promote_next_speaking_request()
            if queue_before_promote != len(self.speaking_queue) or (
                queue_before_promote > 0 and self.current_speaking_request is not None
            ):
                changed = True
        return changed


class ConsoleHubLogger:
    """Small logger compatible with the transport and hub service."""

    def __init__(self, *, verbose: bool = False) -> None:
        self._verbose = verbose

    def info(self, message: str) -> None:
        print(f"[hub][info] {message}", flush=True)

    def warning(self, message: str) -> None:
        print(f"[hub][warn] {message}", flush=True)

    def error(self, message: str) -> None:
        print(f"[hub][error] {message}", flush=True)

    def debug(self, message: str) -> None:
        if self._verbose:
            print(f"[hub][debug] {message}", flush=True)


class LiveHubService:
    """Independent Bilibili capture hub with a local web UI."""

    def __init__(self, settings: LiveHubSettings, *, logger: Any | None = None) -> None:
        self._settings = settings
        self._logger = logger or ConsoleHubLogger()
        self._state = HubRuntimeState(
            title=settings.hub.title,
            room_id=int(settings.bilibili.room_id),
            recent_events_limit=int(settings.hub.recent_events_limit),
            history_snapshot_limit=int(settings.hub.history_snapshot_limit),
        )
        self._transport = BilibiliDanmakuTransport(
            on_event=self._handle_transport_event,
            on_connection_opened=self._handle_transport_opened,
            on_connection_closed=self._handle_transport_closed,
            logger=self._logger,
        )
        self._transport.configure(settings.bilibili)
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._subscribers: set[web.WebSocketResponse] = set()
        self._subscriber_lock = asyncio.Lock()
        self._speech_lock = asyncio.Lock()
        self._client_presence_cleanup_task: asyncio.Task[None] | None = None
        self._configure_routes()

    @property
    def settings(self) -> LiveHubSettings:
        return self._settings

    async def start(self) -> None:
        if self._runner is not None:
            return
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            host=self._settings.hub.listen_host,
            port=self._settings.hub.listen_port,
        )
        await self._site.start()
        await self._preload_recent_history()
        await self._transport.start()
        self._client_presence_cleanup_task = asyncio.create_task(
            self._run_client_presence_cleanup(),
            name="live_hub.client_presence_cleanup",
        )
        self._logger.info(
            "Live hub started: "
            f"http://{self._settings.hub.listen_host}:{self._settings.hub.listen_port} "
            f"room_id={self._settings.bilibili.room_id}"
        )

    async def stop(self) -> None:
        await self._close_subscribers()
        cleanup_task = self._client_presence_cleanup_task
        self._client_presence_cleanup_task = None
        if cleanup_task is not None:
            cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await cleanup_task
        with contextlib.suppress(Exception):
            await self._transport.stop()
        if self._site is not None:
            with contextlib.suppress(Exception):
                await self._site.stop()
            self._site = None
        if self._runner is not None:
            with contextlib.suppress(Exception):
                await self._runner.cleanup()
            self._runner = None

    def _configure_routes(self) -> None:
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/api/health", self._handle_health)
        self._app.router.add_get("/api/events", self._handle_recent_events)
        self._app.router.add_post("/api/local-inject", self._handle_local_inject)
        self._app.router.add_post("/api/client/presence", self._handle_client_presence)
        self._app.router.add_post("/api/client/reply", self._handle_client_reply)
        self._app.router.add_post(
            "/api/client/speak-request", self._handle_client_speak_request
        )
        self._app.router.add_post(
            "/api/client/speak-complete", self._handle_client_speak_complete
        )
        self._app.router.add_get("/ws", self._handle_ws)

    async def _handle_index(self, request: web.Request) -> web.Response:
        del request
        html = INDEX_HTML.replace("__TITLE__", self._settings.hub.title)
        html = html.replace("__ROOM_ID__", str(self._settings.bilibili.room_id))
        html = html.replace(
            "__DEFAULT_USERNAME__", self._settings.input.default_username
        )
        html = html.replace(
            "__DEFAULT_USER_ID__",
            _preferred_local_inject_form_user_id(self._settings.input.default_user_id),
        )
        return web.Response(text=html, content_type="text/html")

    async def _handle_health(self, request: web.Request) -> web.Response:
        del request
        payload = self._state.build_health_payload()
        payload["source_adapter_config"] = str(self._settings.source_adapter_config)
        payload["listen_host"] = self._settings.hub.listen_host
        payload["listen_port"] = self._settings.hub.listen_port
        payload["participants"] = self._state.build_participants_payload()
        payload["speaking"] = self._state.build_speaking_payload(
            enabled=self._settings.speech.enabled
        )
        return web.json_response(payload)

    async def _handle_recent_events(self, request: web.Request) -> web.Response:
        limit_raw = request.query.get("limit", "")
        try:
            limit = max(
                1,
                min(int(limit_raw or self._settings.hub.history_snapshot_limit), 1000),
            )
        except ValueError:
            limit = self._settings.hub.history_snapshot_limit
        payload = {
            "events": self._state.build_history_snapshot()[-limit:],
            "health": self._state.build_health_payload(),
            "participants": self._state.build_participants_payload(),
            "speaking": self._state.build_speaking_payload(
                enabled=self._settings.speech.enabled
            ),
        }
        return web.json_response(payload)

    async def _handle_local_inject(self, request: web.Request) -> web.Response:
        payload = await self._read_payload(request)
        text = str(payload.get("text") or "").strip()
        if not text:
            return web.json_response(
                {"ok": False, "error": "text is required"}, status=400
            )
        if not self._settings.input.enabled:
            return web.json_response(
                {"ok": False, "error": "local input is disabled"}, status=403
            )
        username = str(
            payload.get("username") or self._settings.input.default_username
        ).strip()
        user_id = str(
            payload.get("user_id") or self._settings.input.default_user_id
        ).strip()
        event = build_local_injected_event(
            text=text,
            username=username,
            user_id=user_id,
        )
        record = await self.publish_event(event, origin="local_input")
        return web.json_response({"ok": True, "event": record})

    async def _handle_client_presence(self, request: web.Request) -> web.Response:
        payload = _normalize_client_presence_payload(await self._read_payload(request))
        if not self._settings.client_api.enabled:
            return web.json_response(
                {"ok": False, "error": "client API is disabled"}, status=403
            )
        client_id = str(payload.get("client_id") or "").strip()
        if not client_id:
            return web.json_response(
                {"ok": False, "error": "client_id is required"}, status=400
            )
        participant = await self._refresh_client_presence(payload)
        return web.json_response(
            {
                "ok": True,
                "participant": participant,
                "participants": self._state.build_participants_payload(),
                "health": self._state.build_health_payload(),
                "speaking": self._state.build_speaking_payload(
                    enabled=self._settings.speech.enabled
                ),
            }
        )

    async def _handle_client_reply(self, request: web.Request) -> web.Response:
        payload = _normalize_client_reply_payload(await self._read_payload(request))
        if not self._settings.client_api.enabled:
            return web.json_response(
                {"ok": False, "error": "client API is disabled"}, status=403
            )
        text = str(payload.get("text") or "").strip()
        if not text:
            return web.json_response(
                {"ok": False, "error": "text is required"}, status=400
            )
        participant = await self._refresh_client_presence(payload)
        event = build_client_reply_event(
            client_id=str(payload.get("client_id") or "").strip(),
            bot_name=str(payload.get("bot_name") or "").strip(),
            text=text,
            forward_user_id=str(participant.get("forward_user_id") or "").strip(),
            forward_username=str(participant.get("forward_username") or "").strip(),
            metadata=payload,
        )
        record = await self.publish_event(event, origin="client_reply")
        return web.json_response({"ok": True, "event": record})

    async def _handle_client_speak_request(self, request: web.Request) -> web.Response:
        payload = _normalize_client_speak_payload(await self._read_payload(request))
        if not self._settings.client_api.enabled or not self._settings.speech.enabled:
            return web.json_response(
                {"ok": False, "error": "client speech API is disabled"}, status=403
            )
        client_id = str(payload.get("client_id") or "").strip()
        request_id = str(payload.get("request_id") or "").strip()
        if not client_id:
            return web.json_response(
                {"ok": False, "error": "client_id is required"}, status=400
            )
        if not request_id:
            return web.json_response(
                {"ok": False, "error": "request_id is required"}, status=400
            )
        participant = await self._refresh_client_presence(payload)
        async with self._speech_lock:
            outcome = self._state.request_speaking_turn(
                request_id=request_id,
                client_id=client_id,
                bot_name=str(
                    payload.get("bot_name") or participant.get("bot_name") or client_id
                ).strip(),
                text=str(payload.get("text") or "").strip(),
                expected_duration_ms=int(payload.get("expected_duration_ms") or 0),
            )
        await self._broadcast_health()
        return web.json_response(
            {
                "ok": True,
                "granted": bool(outcome.get("granted")),
                "position": int(outcome.get("position") or 0),
                "participant": participant,
                "participants": self._state.build_participants_payload(),
                "health": self._state.build_health_payload(),
                "speaking": self._state.build_speaking_payload(
                    enabled=self._settings.speech.enabled
                ),
            }
        )

    async def _handle_client_speak_complete(self, request: web.Request) -> web.Response:
        payload = _normalize_client_speak_payload(await self._read_payload(request))
        if not self._settings.client_api.enabled or not self._settings.speech.enabled:
            return web.json_response(
                {"ok": False, "error": "client speech API is disabled"}, status=403
            )
        client_id = str(payload.get("client_id") or "").strip()
        request_id = str(payload.get("request_id") or "").strip()
        if not client_id:
            return web.json_response(
                {"ok": False, "error": "client_id is required"}, status=400
            )
        if not request_id:
            return web.json_response(
                {"ok": False, "error": "request_id is required"}, status=400
            )
        async with self._speech_lock:
            released = self._state.complete_speaking_turn(
                request_id=request_id, client_id=client_id
            )
        if released:
            await self._broadcast_health()
        return web.json_response(
            {
                "ok": True,
                "released": released,
                "participants": self._state.build_participants_payload(),
                "health": self._state.build_health_payload(),
                "speaking": self._state.build_speaking_payload(
                    enabled=self._settings.speech.enabled
                ),
            }
        )

    async def _handle_ws(self, request: web.Request) -> web.StreamResponse:
        ws = web.WebSocketResponse(heartbeat=20.0)
        await ws.prepare(request)
        async with self._subscriber_lock:
            self._subscribers.add(ws)
        await ws.send_json(
            {
                "kind": "snapshot",
                "events": self._state.build_history_snapshot(),
                "health": self._state.build_health_payload(),
                "participants": self._state.build_participants_payload(),
                "speaking": self._state.build_speaking_payload(
                    enabled=self._settings.speech.enabled
                ),
            }
        )
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    if str(message.data).strip().casefold() == "ping":
                        await ws.send_json({"kind": "pong", "now": time.time()})
                elif message.type in {
                    WSMsgType.ERROR,
                    WSMsgType.CLOSE,
                    WSMsgType.CLOSED,
                }:
                    break
        finally:
            async with self._subscriber_lock:
                self._subscribers.discard(ws)
            with contextlib.suppress(Exception):
                await ws.close()
        return ws

    async def _handle_transport_event(self, event: Mapping[str, Any]) -> None:
        await self.publish_event(event, origin="bilibili")

    async def _handle_transport_opened(self) -> None:
        self._state.record_connection_state(connected=True)
        await self._broadcast_health()

    async def _handle_transport_closed(self) -> None:
        self._state.record_connection_state(connected=False)
        await self._broadcast_health()

    async def publish_event(
        self, event: Mapping[str, Any], *, origin: str
    ) -> dict[str, Any]:
        record = self._state.record_public_event(event, origin=origin)
        await self._broadcast_json(
            {
                "kind": "event",
                "event": record,
                "health": self._state.build_health_payload(),
                "participants": self._state.build_participants_payload(),
                "speaking": self._state.build_speaking_payload(
                    enabled=self._settings.speech.enabled
                ),
            }
        )
        if origin == "bilibili":
            self._logger.debug(
                f"Captured event seq={record['seq']} type={record['type']} user={record['username']} text={record['text'][:80]!r}"
            )
        return record

    async def _preload_recent_history(self) -> None:
        if not bool(self._settings.hub.preload_history_on_startup):
            return
        limit = max(1, int(self._settings.hub.preload_history_limit))
        payload = await self._fetch_history_payload_for_preload()
        if payload is None:
            return
        events = extract_history_events(payload)
        if not events:
            return
        events = sorted(
            events, key=lambda item: _normalize_public_timestamp(item.get("timestamp"))
        )[-limit:]
        for event in events:
            self._state.record_public_event(event, origin="bilibili")
        self._logger.info(
            f"Preloaded {len(events)} recent history event(s) into the hub view."
        )

    async def _fetch_history_payload_for_preload(self) -> Mapping[str, Any] | None:
        ensure_session = getattr(self._transport, "_ensure_session", None)
        fetch_history = getattr(self._transport, "_fetch_history_payload", None)
        if not callable(ensure_session) or not callable(fetch_history):
            return None
        try:
            await ensure_session()
            payload = await fetch_history()
        except Exception as exc:
            self._logger.warning(f"Failed to preload recent Bilibili history: {exc}")
            return None
        return payload if isinstance(payload, Mapping) else None

    async def _refresh_client_presence(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        client_id = str(payload.get("client_id") or "").strip()
        bot_name = (
            str(payload.get("bot_name") or "").strip() or client_id or "hub-client"
        )
        mapping = self._resolve_client_identity_mapping(client_id)
        forward_user_id = (
            str(mapping.forward_user_id or "").strip()
            or str(payload.get("forward_user_id") or "").strip()
            or client_id
            or "hub-client"
        )
        forward_username = (
            str(mapping.forward_username or "").strip()
            or str(payload.get("forward_username") or "").strip()
            or bot_name
            or forward_user_id
        )
        changed = self._state.upsert_client_presence(
            client_id=client_id,
            bot_name=bot_name,
            forward_user_id=forward_user_id,
            forward_username=forward_username,
        )
        participant = next(
            (
                item
                for item in self._state.build_participants_payload()
                if str(item.get("client_id") or "").strip() == client_id
            ),
            {
                "client_id": client_id,
                "bot_name": bot_name,
                "forward_user_id": forward_user_id,
                "forward_username": forward_username,
            },
        )
        if changed:
            await self._broadcast_health()
        return participant

    def _resolve_client_identity_mapping(self, client_id: str) -> ClientIdentityMapping:
        normalized_client_id = str(client_id or "").strip()
        if not normalized_client_id:
            return ClientIdentityMapping()
        return self._settings.client_mappings.get(
            normalized_client_id, ClientIdentityMapping()
        )

    async def _run_client_presence_cleanup(self) -> None:
        ttl_sec = max(3.0, float(self._settings.client_api.presence_ttl_sec or 30))
        sleep_sec = max(1.0, min(ttl_sec / 3.0, 10.0))
        while True:
            await asyncio.sleep(sleep_sec)
            clients_changed = self._state.prune_stale_clients(ttl_sec=ttl_sec)
            async with self._speech_lock:
                speaking_changed = self._state.prune_stale_speaking_requests(
                    active_client_ids=set(self._state.active_clients),
                    stale_speaker_timeout_sec=float(
                        self._settings.speech.stale_speaker_timeout_sec or 180
                    ),
                )
            if clients_changed or speaking_changed:
                await self._broadcast_health()

    async def _broadcast_health(self) -> None:
        await self._broadcast_json(
            {
                "kind": "health",
                "health": self._state.build_health_payload(),
                "participants": self._state.build_participants_payload(),
                "speaking": self._state.build_speaking_payload(
                    enabled=self._settings.speech.enabled
                ),
            }
        )

    async def _broadcast_json(self, payload: Mapping[str, Any]) -> None:
        async with self._subscriber_lock:
            subscribers = tuple(self._subscribers)
        if not subscribers:
            return
        stale: list[web.WebSocketResponse] = []
        for ws in subscribers:
            if ws.closed:
                stale.append(ws)
                continue
            try:
                await ws.send_str(json.dumps(payload, ensure_ascii=False, default=str))
            except Exception:
                stale.append(ws)
        if stale:
            async with self._subscriber_lock:
                for ws in stale:
                    self._subscribers.discard(ws)
                    with contextlib.suppress(Exception):
                        await ws.close()

    async def _close_subscribers(self) -> None:
        async with self._subscriber_lock:
            subscribers = tuple(self._subscribers)
            self._subscribers.clear()
        for ws in subscribers:
            with contextlib.suppress(Exception):
                await ws.close()

    @staticmethod
    async def _read_payload(request: web.Request) -> dict[str, Any]:
        content_type = str(request.content_type or "").strip().casefold()
        if content_type == "application/json":
            payload = await request.json()
            return dict(payload) if isinstance(payload, Mapping) else {}
        if (
            content_type == "application/x-www-form-urlencoded"
            or content_type.startswith("multipart/")
        ):
            form = await request.post()
            return dict(form)
        try:
            payload = await request.json()
            return dict(payload) if isinstance(payload, Mapping) else {}
        except Exception:
            return {}


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root {
      --bg: #f4efe6;
      --panel: rgba(255, 252, 247, 0.92);
      --panel-strong: #fffaf2;
      --ink: #1f2430;
      --muted: #6a7280;
      --line: rgba(31, 36, 48, 0.12);
      --accent: #2e74b5;
      --accent-soft: rgba(46, 116, 181, 0.14);
      --warn: #c75c2a;
      --local: #0d8f6b;
      --reply: #7a4ee0;
      --shadow: 0 18px 48px rgba(44, 42, 38, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(46, 116, 181, 0.14), transparent 32%),
        radial-gradient(circle at top right, rgba(199, 92, 42, 0.10), transparent 28%),
        linear-gradient(180deg, #f8f3ea 0%, var(--bg) 100%);
    }
    .shell {
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px 18px 32px;
    }
    .hero {
      display: grid;
      gap: 14px;
      margin-bottom: 18px;
    }
    .title {
      display: flex;
      align-items: baseline;
      gap: 14px;
      flex-wrap: wrap;
    }
    h1 {
      margin: 0;
      font-size: clamp(30px, 5vw, 42px);
      line-height: 1;
      letter-spacing: -0.04em;
    }
    .tag {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      color: var(--muted);
      font-size: 14px;
    }
    .grid {
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(8px);
    }
    .card-inner {
      padding: 18px;
      display: grid;
      gap: 14px;
    }
    .section-title {
      margin: 0;
      font-size: 14px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .stat {
      padding: 12px;
      border-radius: 16px;
      background: var(--panel-strong);
      border: 1px solid var(--line);
    }
    .stat b {
      display: block;
      font-size: 22px;
      margin-bottom: 4px;
    }
    .stat span {
      color: var(--muted);
      font-size: 13px;
    }
    .status-row {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: #999;
      box-shadow: 0 0 0 6px rgba(153, 153, 153, 0.12);
    }
    .status-dot.online {
      background: var(--local);
      box-shadow: 0 0 0 6px rgba(13, 143, 107, 0.14);
    }
    .status-dot.offline {
      background: var(--warn);
      box-shadow: 0 0 0 6px rgba(199, 92, 42, 0.14);
    }
    label {
      display: grid;
      gap: 6px;
      font-size: 14px;
      color: var(--muted);
    }
    input, button {
      font: inherit;
    }
    input {
      width: 100%;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: #fff;
      padding: 12px 14px;
      color: var(--ink);
    }
    button {
      border: 0;
      border-radius: 16px;
      background: linear-gradient(135deg, #2e74b5, #4693d7);
      color: #fff;
      padding: 12px 16px;
      font-weight: 700;
      cursor: pointer;
      transition: transform 120ms ease, box-shadow 120ms ease;
      box-shadow: 0 14px 26px rgba(46, 116, 181, 0.22);
    }
    button:hover {
      transform: translateY(-1px);
    }
    .feed {
      height: min(78vh, 860px);
      min-height: 0;
      display: grid;
      grid-template-rows: auto 1fr;
      overflow: hidden;
    }
    .feed-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 18px 18px 0;
    }
    .feed-list {
      list-style: none;
      margin: 0;
      padding: 18px;
      display: grid;
      gap: 12px;
      min-height: 0;
      overflow: auto;
      overscroll-behavior: contain;
    }
    .event {
      padding: 14px 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.82);
      display: grid;
      gap: 8px;
    }
    .event.bilibili { border-left: 6px solid var(--accent); }
    .event.local_input { border-left: 6px solid var(--local); }
    .event.client_reply { border-left: 6px solid var(--reply); }
    .event-meta {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 13px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      padding: 5px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 700;
    }
    .event.local_input .pill {
      background: rgba(13, 143, 107, 0.14);
      color: var(--local);
    }
    .event.client_reply .pill {
      background: rgba(122, 78, 224, 0.14);
      color: var(--reply);
    }
    .event-text {
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.5;
      font-size: 15px;
    }
    .muted {
      color: var(--muted);
      font-size: 13px;
    }
    @media (max-width: 900px) {
      .grid {
        grid-template-columns: 1fr;
      }
      .feed {
        height: min(60vh, 680px);
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="title">
        <h1>__TITLE__</h1>
        <div class="tag">Room <span id="room-id">__ROOM_ID__</span></div>
      </div>
      <div class="muted">独立 Hub 只负责抓直播弹幕、显示共享事件流、并提供本地注入入口。当前阶段不会接管任何现有实例。</div>
    </section>

    <section class="grid">
      <aside class="card">
        <div class="card-inner">
          <div>
            <p class="section-title">Transport</p>
            <div class="status-row">
              <span id="status-dot" class="status-dot"></span>
              <strong id="status-text">connecting</strong>
            </div>
            <div class="muted" id="status-detail">正在等待第一批弹幕...</div>
          </div>

          <div class="stats">
            <div class="stat"><b id="stat-events">0</b><span>Total events</span></div>
            <div class="stat"><b id="stat-danmaku">0</b><span>Danmaku</span></div>
            <div class="stat"><b id="stat-local">0</b><span>Local inject</span></div>
            <div class="stat"><b id="stat-opens">0</b><span>Connections</span></div>
          </div>

          <div>
            <p class="section-title">Local Inject</p>
            <label>
              昵称
              <input id="username" type="text" value="__DEFAULT_USERNAME__" maxlength="40">
            </label>
            <label>
              User ID
              <input id="user-id" type="text" value="__DEFAULT_USER_ID__" maxlength="80" placeholder="留空则回退到昵称">
            </label>
            <label>
              文本
              <input id="message" type="text" maxlength="500" placeholder="输入一条本地注入消息">
            </label>
            <button id="send-btn" type="button">注入到共享流</button>
            <div class="muted" id="inject-status">本地输入会以普通群聊消息的形式进入 Hub 事件流。</div>
          </div>
        </div>
      </aside>

      <section class="card feed">
        <div class="feed-head">
          <div>
            <p class="section-title">Event Stream</p>
            <div class="muted">实时显示 Hub 捕获到的直播弹幕、本地注入消息，以及后续 client 回传回复。</div>
          </div>
          <div class="muted" id="last-event">暂无事件</div>
        </div>
        <ul id="feed" class="feed-list"></ul>
      </section>
    </section>
  </div>

  <script>
    const feed = document.getElementById("feed");
    const sendBtn = document.getElementById("send-btn");
    const usernameInput = document.getElementById("username");
    const userIdInput = document.getElementById("user-id");
    const messageInput = document.getElementById("message");
    const injectStatus = document.getElementById("inject-status");

    function setHealth(health) {
      if (!health) return;
      document.getElementById("stat-events").textContent = String(health.total_events ?? 0);
      document.getElementById("stat-danmaku").textContent = String(health.total_danmaku_events ?? 0);
      document.getElementById("stat-local").textContent = String(health.local_injected_events ?? 0);
      document.getElementById("stat-opens").textContent = String(health.connection_open_count ?? 0);

      const dot = document.getElementById("status-dot");
      const text = document.getElementById("status-text");
      const detail = document.getElementById("status-detail");
      dot.classList.remove("online", "offline");
      if (health.connected) {
        dot.classList.add("online");
        text.textContent = "connected";
      } else {
        dot.classList.add("offline");
        text.textContent = "disconnected";
      }
      const lastDanmaku = health.last_danmaku_at
        ? new Date(health.last_danmaku_at * 1000).toLocaleTimeString()
        : "none";
      detail.textContent = `最近弹幕: ${lastDanmaku} · 已开连接 ${health.connection_open_count ?? 0} 次`;
    }

    function appendEvent(record) {
      if (!record) return;
      const item = document.createElement("li");
      item.className = `event ${record.origin || "bilibili"}`;
      item.innerHTML = `
        <div class="event-meta">
          <span class="pill">${record.origin || "bilibili"}</span>
          <span>${record.display_time || ""}</span>
          <span>${record.type || "event"}</span>
          <span>${record.username || "anonymous"}</span>
        </div>
        <div class="event-text">${escapeHtml(record.text || "")}</div>
      `;
      feed.appendChild(item);
      while (feed.children.length > 400) {
        feed.removeChild(feed.firstElementChild);
      }
      feed.scrollTop = feed.scrollHeight;
      document.getElementById("last-event").textContent =
        `${record.display_time || ""} · ${record.username || "anonymous"} · ${record.type || "event"}`;
    }

    function escapeHtml(value) {
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    async function loadInitial() {
      const response = await fetch("/api/events");
      const payload = await response.json();
      feed.innerHTML = "";
      (payload.events || []).forEach(appendEvent);
      setHealth(payload.health);
    }

    async function injectLocalMessage() {
      const username = usernameInput.value.trim() || "__DEFAULT_USERNAME__";
      const userId = userIdInput.value.trim();
      const text = messageInput.value.trim();
      if (!text) {
        injectStatus.textContent = "请输入要注入的文本。";
        return;
      }
      sendBtn.disabled = true;
      try {
        const response = await fetch("/api/local-inject", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username, user_id: userId, text }),
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          injectStatus.textContent = payload.error || "注入失败。";
          return;
        }
        injectStatus.textContent = "已注入到共享流。";
        messageInput.value = "";
        messageInput.focus();
      } catch (error) {
        injectStatus.textContent = `注入失败: ${error}`;
      } finally {
        sendBtn.disabled = false;
      }
    }

    function connectWs() {
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${protocol}://${location.host}/ws`);
      ws.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        if (payload.kind === "snapshot") {
          feed.innerHTML = "";
          (payload.events || []).forEach(appendEvent);
          setHealth(payload.health);
          return;
        }
        if (payload.kind === "event") {
          appendEvent(payload.event);
          setHealth(payload.health);
          return;
        }
        if (payload.kind === "health") {
          setHealth(payload.health);
        }
      };
      ws.onclose = () => {
        document.getElementById("status-text").textContent = "reconnecting";
        setTimeout(connectWs, 1500);
      };
    }

    sendBtn.addEventListener("click", injectLocalMessage);
    messageInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        injectLocalMessage();
      }
    });

    loadInitial().then(connectWs).catch((error) => {
      injectStatus.textContent = `初始化失败: ${error}`;
      connectWs();
    });
    setInterval(async () => {
      try {
        const response = await fetch("/api/health");
        const payload = await response.json();
        setHealth(payload);
      } catch (error) {
        console.warn(error);
      }
    }, 5000);
  </script>
</body>
</html>
"""
