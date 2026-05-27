"""Message conversion between Bilibili live events and MaiBot MessageDict."""

from __future__ import annotations

from typing import Any, Mapping

import math
import re
import time
from uuid import uuid4

from src.common.utils.utils_session import SessionUtils
from src.config.config import global_config

from .config import LiveAdapterSettings
from .constants import PLATFORM_NAME


_MODEL_RESERVED_TOKEN_RE = re.compile(r"<[|｜][^<>\r\n]{0,128}[|｜]>")


def sanitize_model_reserved_tokens(text: str) -> str:
    """Remove LLM provider control tokens that make chat messages invalid."""

    sanitized = _MODEL_RESERVED_TOKEN_RE.sub(" ", str(text or ""))
    sanitized = re.sub(r"[ \t\f\v]+", " ", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip()


def build_message_dict(event: Mapping[str, Any], settings: LiveAdapterSettings, *, reason: str = "") -> dict[str, Any]:
    """Build a MaiBot MessageDict from a selected live event."""

    user_id = str(event.get("user_id") or "anonymous").strip()
    username = str(event.get("username") or user_id).strip()
    room_id = str(settings.bilibili.room_id)
    text = sanitize_model_reserved_tokens(str(event.get("text") or event.get("summary") or ""))
    message_id = str(event.get("event_id") or f"bilibili-live-{uuid4().hex}").strip()
    timestamp = _normalize_epoch_seconds(event.get("timestamp"))
    qq_account = str(getattr(getattr(global_config, "bot", None), "qq_account", "") or "").strip()
    memory_chat_id = SessionUtils.calculate_session_id(
        "qq",
        group_id=room_id,
        account_id=qq_account if qq_account not in {"", "0"} else None,
    )
    additional_config = {
        "platform_io_account_id": settings.identity.bot_user_id,
        "platform_io_scope": settings.route_scope(),
        "live_event_type": str(event.get("type") or ""),
        "live_selection_reason": reason,
        "maibot_memory_platform": "qq",
        "maibot_memory_user_id": user_id,
        "maibot_memory_group_id": room_id,
        "maibot_memory_chat_id": memory_chat_id,
        "maibot_local_render_only": True,
    }
    return {
        "message_id": message_id,
        "timestamp": str(timestamp),
        "platform": PLATFORM_NAME,
        "message_info": {
            "user_info": {
                "user_id": user_id,
                "user_nickname": username,
                "user_cardname": None,
            },
            "group_info": {
                "group_id": room_id,
                "group_name": f"bilibili_live_{room_id}",
            },
            "additional_config": additional_config,
        },
        "raw_message": [{"type": "text", "data": text}],
        "is_mentioned": True,
        "is_at": True,
        "is_emoji": False,
        "is_picture": False,
        "is_command": text.startswith("/"),
        "is_notify": False,
        "session_id": "",
        "processed_plain_text": text,
        "display_message": text,
    }


def build_sts2_message_dict(
    settings: LiveAdapterSettings,
    *,
    text: str,
    event_type: str,
    decision_id: str = "",
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a high-priority STS2 system message for MaiBot narration."""

    normalized_text = sanitize_model_reserved_tokens(str(text or ""))
    normalized_event_type = str(event_type or "sts2").strip() or "sts2"
    message_id = f"bilibili-live-{normalized_event_type}-{uuid4().hex}"
    room_id = str(settings.bilibili.room_id)
    qq_account = str(getattr(getattr(global_config, "bot", None), "qq_account", "") or "").strip()
    memory_chat_id = SessionUtils.calculate_session_id(
        "qq",
        group_id=room_id,
        account_id=qq_account if qq_account not in {"", "0"} else None,
    )
    additional_config: dict[str, Any] = {
        "platform_io_account_id": settings.identity.bot_user_id,
        "platform_io_scope": settings.route_scope(),
        "live_event_type": normalized_event_type,
        "live_selection_reason": "sts2_priority",
        "maibot_memory_platform": "qq",
        "maibot_memory_user_id": "sts2-player",
        "maibot_memory_group_id": room_id,
        "maibot_memory_chat_id": memory_chat_id,
        "maibot_local_render_only": True,
        "sts2_priority": True,
        "sts2_payload": dict(payload or {}),
    }
    normalized_decision_id = str(decision_id or "").strip()
    if normalized_decision_id:
        additional_config["sts2_decision_id"] = normalized_decision_id
    return {
        "message_id": message_id,
        "timestamp": str(time.time()),
        "platform": PLATFORM_NAME,
        "message_info": {
            "user_info": {
                "user_id": "sts2-player",
                "user_nickname": "STS2",
                "user_cardname": None,
            },
            "group_info": {
                "group_id": room_id,
                "group_name": f"bilibili_live_{room_id}",
            },
            "additional_config": additional_config,
        },
        "raw_message": [{"type": "text", "data": normalized_text}],
        "is_mentioned": True,
        "is_at": True,
        "is_emoji": False,
        "is_picture": False,
        "is_command": False,
        "is_notify": False,
        "session_id": "",
        "processed_plain_text": normalized_text,
        "display_message": normalized_text,
    }


def build_local_voice_message_dict(
    settings: LiveAdapterSettings,
    *,
    text: str,
    event_id: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a direct-injection MessageDict for local microphone transcripts."""

    local_voice = settings.local_voice
    normalized_event_id = str(event_id or f"local-voice-{uuid4().hex}").strip()
    event = {
        "event_id": normalized_event_id,
        "type": "local_voice",
        "text": text,
        "summary": text,
        "user_id": local_voice.speaker_user_id,
        "username": local_voice.speaker_username,
        "timestamp": time.time(),
    }
    message = build_message_dict(event, settings, reason="local_voice_priority")
    additional_config = message["message_info"]["additional_config"]
    additional_config["local_voice_input"] = True
    additional_config["local_voice_priority"] = True
    if metadata:
        additional_config["local_voice_metadata"] = dict(metadata)
    return message


def _normalize_epoch_seconds(value: Any) -> float:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return time.time()
    if not math.isfinite(timestamp) or timestamp <= 0:
        return time.time()
    while timestamp > 32_503_680_000:
        timestamp /= 1000.0
    return timestamp


def extract_text_from_message(message: Mapping[str, Any]) -> str:
    """Extract displayable text from a MaiBot MessageDict-like mapping."""

    raw_message = message.get("raw_message", [])
    parts: list[str] = []
    if isinstance(raw_message, list):
        for segment in raw_message:
            if not isinstance(segment, Mapping):
                continue
            segment_type = str(segment.get("type") or "").strip()
            data = segment.get("data")
            if segment_type == "text":
                parts.append(str(data or ""))
            elif isinstance(data, Mapping) and "text" in data:
                parts.append(str(data.get("text") or ""))
            elif isinstance(data, str):
                parts.append(data)
    if not parts:
        for key in ("processed_plain_text", "display_message", "plain_text"):
            value = message.get(key)
            if value:
                parts.append(str(value))
                break
    return "".join(parts).strip()


def extract_live_output_text_from_message(message: Mapping[str, Any]) -> str:
    """Extract speech-safe text for subtitle/TTS/Live2D output."""

    raw_message = message.get("raw_message", [])
    parts: list[str] = []
    reply_targets: list[str] = []
    has_reply_segment = False
    if isinstance(raw_message, list):
        for segment in raw_message:
            if not isinstance(segment, Mapping):
                continue
            segment_type = str(segment.get("type") or "").strip()
            data = segment.get("data")
            if segment_type == "reply":
                has_reply_segment = True
                if isinstance(data, Mapping):
                    target_content = str(data.get("target_message_content") or "").strip()
                    if target_content:
                        reply_targets.append(target_content)
                continue
            if segment_type == "text":
                parts.append(str(data or ""))
            elif isinstance(data, Mapping) and "text" in data:
                parts.append(str(data.get("text") or ""))
            elif isinstance(data, str):
                parts.append(data)
    if parts:
        return _sanitize_live_output_text("".join(parts).strip())
    for key in ("processed_plain_text", "display_message", "plain_text"):
        value = message.get(key)
        if value:
            return _sanitize_live_output_text(
                str(value),
                reply_targets=reply_targets,
                has_reply_context=has_reply_segment,
            )
    return ""


def _sanitize_live_output_text(
    text: str,
    *,
    reply_targets: list[str] | None = None,
    has_reply_context: bool = False,
) -> str:
    normalized = _sanitize_legacy_bilibili_reply_tokens(text).strip()
    normalized = _strip_source_rendered_reply_wrappers(normalized)
    if has_reply_context:
        normalized = _strip_leading_reply_target(normalized, reply_targets or [])
    return normalized.strip()


def _sanitize_legacy_bilibili_reply_tokens(text: str) -> str:
    sanitized = str(text or "")
    while True:
        start = sanitized.find("[引用回复](bilibili-history-")
        if start < 0:
            break
        end = sanitized.find(")", start)
        if end < 0:
            break
        sanitized = f"{sanitized[:start]}[引用回复]{sanitized[end + 1:]}"
    while True:
        start = sanitized.find("(bilibili-history-")
        if start < 0:
            break
        end = sanitized.find(")", start)
        if end < 0:
            break
        sanitized = f"{sanitized[:start]}[引用回复]{sanitized[end + 1:]}"
    return sanitized


def _strip_source_rendered_reply_wrappers(text: str) -> str:
    normalized = text.strip()
    while normalized.startswith("["):
        prefix = _split_leading_bracket_token(normalized)
        if prefix is None:
            break
        token, remainder = prefix
        if not _is_source_rendered_reply_token(token):
            break
        normalized = remainder.lstrip()
    return normalized


def _split_leading_bracket_token(text: str) -> tuple[str, str] | None:
    if not text.startswith("["):
        return None
    end = text.find("]")
    if end <= 0:
        return None
    return text[1:end], text[end + 1 :]


def _is_source_rendered_reply_token(token: str) -> bool:
    normalized = token.strip()
    if normalized in {"引用回复", "回复了一条消息，但原消息已无法访问"}:
        return True
    if normalized.startswith("回复消息: "):
        return True
    return normalized.startswith("回复了") and "的消息: " in normalized


def _strip_leading_reply_target(text: str, reply_targets: list[str]) -> str:
    normalized = text.lstrip()
    for target in reply_targets:
        candidate = str(target or "").strip()
        if not candidate:
            continue
        if normalized == candidate:
            return ""
        if not normalized.startswith(candidate):
            continue
        remainder = normalized[len(candidate) :]
        if not remainder:
            return ""
        leading = remainder[0]
        if leading.isspace() or leading in "，。,.!！?？:：;；)]】>》」』":
            return remainder.lstrip(" \t\r\n，。,.!！?？:：;；)]】>》」』")
    return normalized
