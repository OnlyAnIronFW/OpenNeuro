"""Bilibili live danmaku WebSocket transport."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping

import asyncio
import contextlib
import hashlib
import json

try:  # pragma: no cover - availability is environment dependent.
    import aiohttp
except Exception:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]

from .bilibili_codec import build_auth_packet, build_heartbeat_packet, normalize_event, parse_packets
from .config import BilibiliConfig
from .constants import DEFAULT_BILIBILI_WS_URL


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
LifecycleCallback = Callable[[], Awaitable[None]]
HISTORY_POLL_INTERVAL_SEC = 2.0
MAX_HISTORY_EVENT_IDS = 4096
MAX_EVENT_DEDUP_KEYS = 4096


def collect_history_event_ids(payload: Mapping[str, Any]) -> set[str]:
    """Collect normalized history event ids from a Bilibili gethistory payload."""

    event_ids: set[str] = set()
    for event in extract_history_events(payload):
        event_ids.add(str(event["event_id"]))
    return event_ids


def build_history_baseline(payload: Mapping[str, Any] | None) -> set[str] | None:
    """Build the startup baseline used to avoid replaying pre-start history."""

    if payload is None:
        return None
    return collect_history_event_ids(payload)


def extract_history_events(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract normalized history danmaku events from admin/room buckets."""

    data = payload.get("data")
    if not isinstance(data, Mapping):
        return []
    deduped: dict[str, dict[str, Any]] = {}
    for bucket in ("admin", "room"):
        items = data.get(bucket)
        if not isinstance(items, list):
            continue
        for item in items:
            event = _history_item_to_event(item)
            if event is None:
                continue
            deduped.setdefault(str(event["event_id"]), event)
    return list(deduped.values())


def _history_item_to_event(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    text = str(item.get("text") or "").strip()
    if not text:
        return None
    user_id = str(item.get("uid") or "anonymous")
    username = str(item.get("nickname") or item.get("uname") or user_id)
    timeline = str(item.get("timeline") or "").strip()
    timestamp = _parse_history_timeline(timeline)
    raw_event = dict(item)
    return {
        "event_id": _history_event_id(raw_event),
        "type": "danmaku",
        "text": text,
        "summary": text,
        "user_id": user_id,
        "username": username,
        "timestamp": timestamp,
        "raw": raw_event,
    }


def _history_event_id(item: Mapping[str, Any]) -> str:
    id_str = str(item.get("id_str") or "").strip()
    if id_str:
        return f"bilibili-history-{id_str}"
    digest = hashlib.md5(
        json.dumps(dict(item), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"bilibili-history-{digest}"


def _parse_history_timeline(timeline: str) -> float:
    normalized = str(timeline or "").strip()
    if not normalized:
        return datetime.now().timestamp()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt).timestamp()
        except ValueError:
            continue
    return datetime.now().timestamp()


def _event_dedup_key(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("type") or "").strip()
    event_id = str(event.get("event_id") or "").strip()
    user_id = str(event.get("user_id") or "").strip()
    text = str(event.get("text") or event.get("summary") or "").strip()
    timestamp = _normalize_timestamp_bucket(event.get("timestamp"))
    if event_type and user_id and text:
        return f"{event_type}|{user_id}|{text}|{timestamp}"
    if event_id:
        return f"event_id|{event_id}"
    raw = event.get("raw")
    digest = hashlib.md5(
        json.dumps(raw if isinstance(raw, Mapping) else dict(event), ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"raw|{digest}"


def _normalize_timestamp_bucket(value: Any) -> int:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return 0
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return int(timestamp)


class BilibiliDanmakuTransport:
    """Minimal input-only Bilibili live WebSocket client."""

    def __init__(
        self,
        *,
        on_event: EventCallback,
        on_connection_opened: LifecycleCallback | None = None,
        on_connection_closed: LifecycleCallback | None = None,
        logger: Any = None,
    ) -> None:
        self._on_event = on_event
        self._on_connection_opened = on_connection_opened
        self._on_connection_closed = on_connection_closed
        self._logger = logger
        self._config = BilibiliConfig()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._history_task: asyncio.Task[None] | None = None
        self._session: Any = None
        self._ws: Any = None
        self._connection_ready_reported = False
        self._history_fallback_enabled = False
        self._ws_url_candidates: list[str] = []
        self._ws_url_cursor = 0
        self._history_seen_event_ids: set[str] = set()
        self._history_seen_order: deque[str] = deque()
        self._event_dedup_keys: set[str] = set()
        self._event_dedup_order: deque[str] = deque()

    def configure(self, config: BilibiliConfig) -> None:
        """Apply transport config."""

        self._config = config

    def is_available(self) -> bool:
        """Return whether aiohttp is importable."""

        return aiohttp is not None

    async def start(self) -> None:
        """Start the reconnecting receive loop."""

        if not self.is_available():
            self._log_error("aiohttp is required for Bilibili live transport")
            return
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._connection_ready_reported = False
        self._history_fallback_enabled = False
        self._ws_url_candidates.clear()
        self._ws_url_cursor = 0
        self._history_seen_event_ids.clear()
        self._history_seen_order.clear()
        self._event_dedup_keys.clear()
        self._event_dedup_order.clear()
        await self._ensure_session()
        await self._prepare_history_fallback()
        self._task = asyncio.create_task(self._run_loop(), name="bilibili_live.transport")

    async def stop(self) -> None:
        """Stop the receive loop and close network resources."""

        self._running = False
        if self._history_task is not None:
            self._history_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._history_task
        self._history_task = None

        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
        self._heartbeat_task = None

        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        self._ws = None

        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

        await self._reset_session()
        if self._on_connection_closed is not None:
            await self._on_connection_closed()
        self._connection_ready_reported = False
        self._history_fallback_enabled = False
        self._ws_url_candidates.clear()
        self._ws_url_cursor = 0
        self._history_seen_event_ids.clear()
        self._history_seen_order.clear()
        self._event_dedup_keys.clear()
        self._event_dedup_order.clear()

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_warning(f"Bilibili live transport disconnected: {exc}")
            finally:
                if self._history_task is not None:
                    self._history_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._history_task
                    self._history_task = None
                if self._heartbeat_task is not None:
                    self._heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._heartbeat_task
                    self._heartbeat_task = None
                if self._ws is not None:
                    with contextlib.suppress(Exception):
                        await self._ws.close()
                    self._ws = None
                await self._reset_session()
                if self._on_connection_closed is not None:
                    await self._on_connection_closed()
                self._connection_ready_reported = False

            if self._running:
                await asyncio.sleep(max(0.1, float(self._config.reconnect_delay_sec)))

    async def _connect_once(self) -> None:
        assert aiohttp is not None
        await self._ensure_session()
        ws_url, auth_token = await self._resolve_connection_target()
        self._ws = await self._session.ws_connect(ws_url, heartbeat=None)
        self._connection_ready_reported = False
        await self._ws.send_bytes(build_auth_packet(self._config.room_id, uid=self._config.uid, token=auth_token))
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="bilibili_live.heartbeat")

        async for message in self._ws:
            if not self._running:
                break
            if message.type == aiohttp.WSMsgType.BINARY:
                await self._handle_binary(message.data)
            elif message.type == aiohttp.WSMsgType.TEXT:
                await self._handle_binary(message.data.encode("utf-8"))
            elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                break

    async def _heartbeat_loop(self) -> None:
        while self._running and self._ws is not None:
            await asyncio.sleep(max(1.0, float(self._config.heartbeat_interval_sec)))
            if self._ws is None or self._ws.closed:
                return
            await self._ws.send_bytes(build_heartbeat_packet())

    async def _handle_binary(self, payload: bytes) -> None:
        for packet in parse_packets(payload):
            if self._is_successful_auth_reply(packet):
                await self._report_connection_opened_once()
                await self._start_history_polling_if_needed()
                continue
            if str(packet.get("type") or "") == "auth_reply":
                self._log_warning(f"Bilibili live auth failed: {packet.get('raw') or {}}")
                continue
            event = normalize_event(packet)
            if event is not None:
                await self._report_connection_opened_once()
                await self._start_history_polling_if_needed()
                if self._mark_event_seen(event):
                    await self._on_event(event)

    async def _resolve_connection_target(self) -> tuple[str, str]:
        fallback_url = self._config.ws_url or DEFAULT_BILIBILI_WS_URL
        conf = await self._fetch_danmaku_conf()
        if not isinstance(conf, Mapping):
            return self._next_ws_url([fallback_url]), ""
        token = str(conf.get("token") or "").strip()
        resolved_url = self._next_ws_url(self._select_ws_urls(conf) or [fallback_url])
        return resolved_url, token

    async def _fetch_danmaku_conf(self) -> Mapping[str, Any] | None:
        if self._session is None:
            return None
        url = "https://api.live.bilibili.com/room/v1/Danmu/getConf"
        params = {
            "room_id": str(self._config.room_id),
            "platform": "pc",
            "player": "web",
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://live.bilibili.com/{self._config.room_id}/",
        }
        try:
            async with self._session.get(url, params=params, headers=headers) as response:
                payload = await response.json(content_type=None)
        except Exception as exc:
            self._log_warning(f"Bilibili danmaku config request failed: {exc}")
            return None
        if not isinstance(payload, Mapping) or payload.get("code") != 0:
            self._log_warning(f"Bilibili danmaku config request returned: {payload}")
            return None
        data = payload.get("data")
        return data if isinstance(data, Mapping) else None

    async def _fetch_history_payload(self) -> Mapping[str, Any] | None:
        if self._session is None:
            return None
        url = "https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://live.bilibili.com/{self._config.room_id}/",
        }
        try:
            async with self._session.get(url, params={"roomid": str(self._config.room_id)}, headers=headers) as response:
                payload = await response.json(content_type=None)
        except Exception:
            return None
        if not isinstance(payload, Mapping) or payload.get("code") != 0:
            return None
        return payload

    async def _prepare_history_fallback(self) -> None:
        payload = await self._fetch_history_payload()
        baseline = build_history_baseline(payload)
        if baseline is None:
            self._log_warning("Bilibili history fallback disabled because the startup baseline snapshot could not be fetched.")
            return
        self._history_fallback_enabled = True
        for event_id in baseline:
            self._remember_history_event_id(event_id)

    async def _history_poll_loop(self) -> None:
        while self._running:
            await asyncio.sleep(HISTORY_POLL_INTERVAL_SEC)
            payload = await self._fetch_history_payload()
            if payload is None:
                continue
            await self._handle_history_payload(payload)

    async def _handle_history_payload(self, payload: Mapping[str, Any]) -> None:
        for event in extract_history_events(payload):
            event_id = str(event.get("event_id") or "").strip()
            if not event_id or event_id in self._history_seen_event_ids:
                continue
            self._remember_history_event_id(event_id)
            if self._mark_event_seen(event):
                await self._on_event(event)

    async def _start_history_polling_if_needed(self) -> None:
        if not self._history_fallback_enabled:
            return
        if self._history_task is not None and not self._history_task.done():
            return
        self._history_task = asyncio.create_task(self._history_poll_loop(), name="bilibili_live.history_poll")

    async def _ensure_session(self) -> None:
        assert aiohttp is not None
        timeout = aiohttp.ClientTimeout(total=max(1.0, float(self._config.connect_timeout_sec)))
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": self._config.user_agent},
            )

    async def _reset_session(self) -> None:
        if self._session is None:
            return
        with contextlib.suppress(Exception):
            await self._session.close()
        self._session = None

    def _remember_history_event_id(self, event_id: str) -> None:
        if event_id in self._history_seen_event_ids:
            return
        if len(self._history_seen_order) >= MAX_HISTORY_EVENT_IDS:
            oldest = self._history_seen_order.popleft()
            self._history_seen_event_ids.discard(oldest)
        self._history_seen_order.append(event_id)
        self._history_seen_event_ids.add(event_id)

    def _mark_event_seen(self, event: Mapping[str, Any]) -> bool:
        dedup_key = _event_dedup_key(event)
        if dedup_key in self._event_dedup_keys:
            return False
        if len(self._event_dedup_order) >= MAX_EVENT_DEDUP_KEYS:
            oldest = self._event_dedup_order.popleft()
            self._event_dedup_keys.discard(oldest)
        self._event_dedup_order.append(dedup_key)
        self._event_dedup_keys.add(dedup_key)
        return True

    @staticmethod
    def _select_ws_url(conf: Mapping[str, Any]) -> str:
        urls = BilibiliDanmakuTransport._select_ws_urls(conf)
        return urls[0] if urls else ""

    @staticmethod
    def _select_ws_urls(conf: Mapping[str, Any]) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        host_server_list = conf.get("host_server_list")
        if isinstance(host_server_list, list):
            for item in host_server_list:
                if not isinstance(item, Mapping):
                    continue
                host = str(item.get("host") or "").strip()
                if not host:
                    continue
                wss_port = item.get("wss_port") or 443
                url = f"wss://{host}:{int(wss_port)}/sub"
                if url not in seen:
                    urls.append(url)
                    seen.add(url)
        host = str(conf.get("host") or "").strip()
        if host:
            url = f"wss://{host}:443/sub"
            if url not in seen:
                urls.append(url)
        return urls

    def _next_ws_url(self, urls: list[str]) -> str:
        if not urls:
            return ""
        normalized = [url for url in urls if url]
        if not normalized:
            return ""
        if normalized != self._ws_url_candidates:
            self._ws_url_candidates = normalized
            self._ws_url_cursor %= len(normalized)
        index = self._ws_url_cursor % len(normalized)
        self._ws_url_cursor += 1
        return normalized[index]

    async def _report_connection_opened_once(self) -> None:
        if self._connection_ready_reported:
            return
        if self._on_connection_opened is not None:
            await self._on_connection_opened()
        self._connection_ready_reported = True

    @staticmethod
    def _is_successful_auth_reply(packet: Mapping[str, Any]) -> bool:
        if str(packet.get("type") or "") != "auth_reply":
            return False
        raw = packet.get("raw")
        if not isinstance(raw, Mapping):
            return True
        code = raw.get("code")
        return code in {None, 0, "0"}

    def _log_warning(self, message: str) -> None:
        if self._logger is not None:
            self._logger.warning(message)

    def _log_error(self, message: str) -> None:
        if self._logger is not None:
            self._logger.error(message)
