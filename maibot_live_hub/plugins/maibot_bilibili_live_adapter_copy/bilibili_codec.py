"""Bilibili live WebSocket packet codec and event normalizer."""

from __future__ import annotations

from typing import Any, Mapping

import hashlib
import json
import math
import struct
import time
import zlib

from .constants import (
    BILIBILI_OPERATION_AUTH,
    BILIBILI_OPERATION_AUTH_REPLY,
    BILIBILI_OPERATION_HEARTBEAT,
    BILIBILI_OPERATION_HEARTBEAT_REPLY,
    BILIBILI_OPERATION_MESSAGE,
    BILIBILI_PROTOCOL_ZLIB,
)


HEADER_STRUCT = struct.Struct(">IHHII")
HEADER_LENGTH = 16


def build_packet(
    operation: int,
    payload: bytes | str | Mapping[str, Any] | None = None,
    *,
    protocol_version: int = 1,
    sequence: int = 1,
) -> bytes:
    """Build one Bilibili live WebSocket packet."""

    payload_bytes = _to_payload_bytes(payload)
    packet_length = HEADER_LENGTH + len(payload_bytes)
    header = HEADER_STRUCT.pack(packet_length, HEADER_LENGTH, int(protocol_version), int(operation), int(sequence))
    return header + payload_bytes


def build_auth_packet(room_id: int, *, uid: int = 0, token: str = "") -> bytes:
    """Build an anonymous Bilibili live auth packet."""

    payload = {
        "uid": max(0, int(uid)),
        "roomid": max(0, int(room_id)),
        "protover": BILIBILI_PROTOCOL_ZLIB,
        "platform": "web",
        "type": 2,
    }
    if token:
        payload["key"] = token
    return build_packet(BILIBILI_OPERATION_AUTH, payload, protocol_version=1)


def build_heartbeat_packet() -> bytes:
    """Build a Bilibili heartbeat packet."""

    return build_packet(BILIBILI_OPERATION_HEARTBEAT, b"", protocol_version=1)


def parse_packets(data: bytes) -> list[dict[str, Any]]:
    """Parse one or more Bilibili packets from a binary frame."""

    packets: list[dict[str, Any]] = []
    offset = 0
    data_length = len(data)
    while offset + HEADER_LENGTH <= data_length:
        packet_length, header_length, protocol_version, operation, sequence = HEADER_STRUCT.unpack_from(data, offset)
        if packet_length < header_length or header_length < HEADER_LENGTH:
            break
        packet_end = offset + packet_length
        if packet_end > data_length:
            break

        payload = data[offset + header_length : packet_end]
        packets.extend(
            _parse_payload(
                payload,
                operation=operation,
                protocol_version=protocol_version,
                sequence=sequence,
            )
        )
        offset = packet_end
    return packets


def normalize_event(raw_event: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize Bilibili business messages into live adapter events."""

    command = str(raw_event.get("cmd") or "").split(":", 1)[0]
    if command == "DANMU_MSG":
        return _normalize_danmaku(raw_event)
    if command == "SUPER_CHAT_MESSAGE":
        return _normalize_super_chat(raw_event)
    if command == "SEND_GIFT":
        return _normalize_gift(raw_event)
    if command == "GUARD_BUY":
        return _normalize_guard(raw_event)
    return None


def _parse_payload(
    payload: bytes,
    *,
    operation: int,
    protocol_version: int,
    sequence: int,
) -> list[dict[str, Any]]:
    if operation == BILIBILI_OPERATION_MESSAGE and protocol_version == BILIBILI_PROTOCOL_ZLIB:
        try:
            return parse_packets(zlib.decompress(payload))
        except zlib.error:
            return []

    if operation == BILIBILI_OPERATION_HEARTBEAT_REPLY:
        popularity = 0
        if len(payload) >= 4:
            popularity = struct.unpack(">I", payload[:4])[0]
        return [
            {
                "operation": operation,
                "protocol_version": protocol_version,
                "sequence": sequence,
                "type": "heartbeat_reply",
                "popularity": popularity,
            }
        ]

    if operation == BILIBILI_OPERATION_AUTH_REPLY:
        decoded = _decode_json(payload)
        return [
            {
                "operation": operation,
                "protocol_version": protocol_version,
                "sequence": sequence,
                "type": "auth_reply",
                "raw": decoded if isinstance(decoded, Mapping) else {},
            }
        ]

    if operation != BILIBILI_OPERATION_MESSAGE:
        return [
            {
                "operation": operation,
                "protocol_version": protocol_version,
                "sequence": sequence,
                "payload": payload,
            }
        ]

    decoded = _decode_json(payload)
    if isinstance(decoded, list):
        return [dict(item) for item in decoded if isinstance(item, Mapping)]
    if isinstance(decoded, Mapping):
        return [dict(decoded)]
    return []


def _normalize_danmaku(raw_event: Mapping[str, Any]) -> dict[str, Any] | None:
    info = raw_event.get("info")
    if not isinstance(info, list) or len(info) < 3:
        return None
    text = str(info[1] or "").strip()
    if not text:
        return None
    user = info[2] if isinstance(info[2], list) else []
    user_id = str(user[0] if len(user) > 0 else "anonymous")
    username = str(user[1] if len(user) > 1 else user_id)
    return _base_event(
        raw_event,
        event_type="danmaku",
        text=text,
        user_id=user_id,
        username=username,
        summary=text,
    )


def _normalize_super_chat(raw_event: Mapping[str, Any]) -> dict[str, Any] | None:
    data = raw_event.get("data")
    if not isinstance(data, Mapping):
        return None
    text = str(data.get("message") or "").strip()
    user_info = data.get("user_info") if isinstance(data.get("user_info"), Mapping) else {}
    user_id = str(data.get("uid") or user_info.get("uid") or "anonymous")
    username = str(user_info.get("uname") or data.get("uname") or user_id)
    price = _as_float(data.get("price"))
    summary = f"SC {price:g}: {text}" if price else text
    event = _base_event(
        raw_event,
        event_type="super_chat",
        text=text,
        user_id=user_id,
        username=username,
        summary=summary,
    )
    event["price"] = price
    return event


def _normalize_gift(raw_event: Mapping[str, Any]) -> dict[str, Any] | None:
    data = raw_event.get("data")
    if not isinstance(data, Mapping):
        return None
    gift_name = str(data.get("giftName") or data.get("gift_name") or "gift").strip()
    count = max(1, int(_as_float(data.get("num"), default=1.0)))
    username = str(data.get("uname") or data.get("username") or data.get("uid") or "anonymous")
    user_id = str(data.get("uid") or "anonymous")
    summary = f"{username} sent {gift_name} x{count}"
    event = _base_event(
        raw_event,
        event_type="gift",
        text=summary,
        user_id=user_id,
        username=username,
        summary=summary,
    )
    event.update({"gift_name": gift_name, "count": count, "price": _as_float(data.get("price"))})
    return event


def _normalize_guard(raw_event: Mapping[str, Any]) -> dict[str, Any] | None:
    data = raw_event.get("data")
    if not isinstance(data, Mapping):
        return None
    username = str(data.get("username") or data.get("uname") or data.get("uid") or "anonymous")
    user_id = str(data.get("uid") or "anonymous")
    gift_name = str(data.get("gift_name") or data.get("giftName") or "guard").strip()
    count = max(1, int(_as_float(data.get("num"), default=1.0)))
    summary = f"{username} bought {gift_name} x{count}"
    event = _base_event(
        raw_event,
        event_type="guard",
        text=summary,
        user_id=user_id,
        username=username,
        summary=summary,
    )
    event.update({"gift_name": gift_name, "count": count})
    return event


def _base_event(
    raw_event: Mapping[str, Any],
    *,
    event_type: str,
    text: str,
    user_id: str,
    username: str,
    summary: str,
) -> dict[str, Any]:
    event_id = _event_id(raw_event)
    return {
        "event_id": event_id,
        "type": event_type,
        "text": text,
        "summary": summary,
        "user_id": user_id,
        "username": username,
        "timestamp": _extract_timestamp(raw_event),
        "raw": dict(raw_event),
    }


def _extract_timestamp(raw_event: Mapping[str, Any]) -> float:
    data = raw_event.get("data")
    if isinstance(data, Mapping):
        for key in ("ts", "timestamp", "start_time"):
            value = data.get(key)
            if value:
                return _normalize_epoch_seconds(value)
    info = raw_event.get("info")
    if isinstance(info, list) and info:
        meta = info[0]
        if isinstance(meta, list) and len(meta) > 4:
            return _normalize_epoch_seconds(meta[4])
    return time.time()


def _event_id(raw_event: Mapping[str, Any]) -> str:
    data = raw_event.get("data")
    if isinstance(data, Mapping):
        for key in ("id", "message_id", "msg_id"):
            value = str(data.get(key) or "").strip()
            if value:
                return f"bilibili-{value}"
    digest = hashlib.md5(
        json.dumps(raw_event, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"bilibili-{digest}"


def _decode_json(payload: bytes) -> Any:
    if not payload:
        return {}
    text = payload.decode("utf-8", errors="ignore").strip("\x00\r\n\t ")
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _to_payload_bytes(payload: bytes | str | Mapping[str, Any] | None) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _as_float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _normalize_epoch_seconds(value: Any) -> float:
    timestamp = _as_float(value, default=time.time())
    if not math.isfinite(timestamp) or timestamp <= 0:
        return time.time()
    while timestamp > 32_503_680_000:
        timestamp /= 1000.0
    return timestamp
