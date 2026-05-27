"""原生桌面字幕 UI 服务入口。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import asyncio
import time

from .subtitle_native_runtime import SubtitleNativeUIRuntime
from .subtitle_native_state import (
    DEFAULT_SUBTITLE_UI_SETTINGS,
    SubtitleUISettingsStore,
    normalize_subtitle_ui_settings,
    subtitle_defaults_to_settings,
)


@dataclass(frozen=True)
class SubtitleSegment:
    """单条字幕分段。"""

    index: int
    text: str
    duration_ms: int
    audio_ref: str = ""
    audio_url: str = ""
    provider: str = ""
    speech_text: str = ""


def estimate_subtitle_duration_ms(text: str, *, chars_per_second: float = 7.5) -> int:
    """在没有真实音频时，估算一条字幕的可读时长。"""

    normalized_text = str(text or "").strip()
    if not normalized_text:
        return 500
    base_duration_ms = int(max(500.0, len(normalized_text) / max(1.0, float(chars_per_second)) * 1000.0))
    punctuation_pause_ms = sum(_punctuation_pause_ms(char) for char in normalized_text)
    return base_duration_ms + punctuation_pause_ms


def build_subtitle_reply_payload(
    *,
    reply_id: str,
    text: str,
    segments: list[SubtitleSegment],
    source_platform: str = "",
) -> dict[str, Any]:
    """构建供原生字幕运行时消费的 reply payload。"""

    return {
        "type": "subtitle.reply",
        "reply_id": reply_id,
        "text": str(text or ""),
        "source_platform": str(source_platform or ""),
        "created_at_ms": int(time.time() * 1000),
        "retention_policy": "trim_oldest",
        "segments": [asdict(segment) for segment in segments],
    }


class SubtitleWebUIService:
    """保持插件侧 API 稳定的原生字幕 UI 服务。"""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        subtitle_defaults: Mapping[str, Any] | None = None,
        logger: Any = None,
    ) -> None:
        self.host = str(host or "").strip() or "127.0.0.1"
        self.port = max(1, int(port or 18182))
        self.subtitle_defaults = normalize_subtitle_ui_settings(
            subtitle_defaults_to_settings(subtitle_defaults),
            defaults=DEFAULT_SUBTITLE_UI_SETTINGS,
        )
        self.logger = logger
        self._runtime: SubtitleNativeUIRuntime | None = None
        self._audio_start_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._audio_start_events: dict[str, dict[str, Any]] = {}
        self._event_loop: asyncio.AbstractEventLoop | None = None

    @property
    def has_clients(self) -> bool:
        """原生 UI 运行中时视为已连接。"""

        return bool(self._runtime is not None and self._runtime.is_running)

    async def start(self) -> None:
        """启动原生字幕窗口运行时。"""

        if self._runtime is not None and self._runtime.is_running:
            return
        self._event_loop = asyncio.get_running_loop()
        settings_store = SubtitleUISettingsStore(_plugin_data_dir() / "subtitle_ui_settings.json", defaults=self.subtitle_defaults)
        runtime = SubtitleNativeUIRuntime(
            settings_store=settings_store,
            logger=self.logger,
            on_audio_started=self.handle_audio_started,
        )
        runtime.start()
        self._runtime = runtime
        self._log_info("Subtitle native UI started")

    async def stop(self) -> None:
        """停止原生字幕运行时，并清理等待中的 ACK。"""

        runtime = self._runtime
        self._runtime = None
        if runtime is not None:
            runtime.stop()
        for waiter in self._audio_start_waiters.values():
            if not waiter.done():
                waiter.cancel()
        self._audio_start_waiters.clear()
        self._audio_start_events.clear()

    def register_audio_asset(self, path: Path) -> str:
        """注册本地音频资源，原生 UI 直接返回绝对路径。"""

        resolved_path = Path(path).expanduser().resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"Subtitle audio asset does not exist: {resolved_path}")
        return str(resolved_path)

    async def publish_reply(
        self,
        *,
        reply_id: str,
        text: str,
        segments: list[SubtitleSegment],
        source_platform: str = "",
    ) -> None:
        """将一条 reply 投递给原生 UI。"""

        if not segments:
            return
        runtime = self._runtime
        if runtime is None:
            return
        payload = build_subtitle_reply_payload(
            reply_id=reply_id,
            text=text,
            segments=segments,
            source_platform=source_platform,
        )
        runtime.enqueue_reply(payload)

    async def wait_for_audio_start(self, reply_id: str, *, timeout_sec: float = 2.5) -> dict[str, Any] | None:
        """等待字幕音频开始事件。"""

        normalized_reply_id = str(reply_id or "").strip()
        if not normalized_reply_id:
            return None
        cached_event = self._audio_start_events.get(normalized_reply_id)
        if cached_event is not None:
            return dict(cached_event)
        waiter = asyncio.get_running_loop().create_future()
        self._audio_start_waiters[normalized_reply_id] = waiter
        try:
            return await asyncio.wait_for(waiter, timeout=max(0.05, float(timeout_sec)))
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return None
        finally:
            if self._audio_start_waiters.get(normalized_reply_id) is waiter:
                self._audio_start_waiters.pop(normalized_reply_id, None)

    def handle_audio_started(self, reply_id: str, *, segment_index: int = 0, started_at_ms: int | None = None) -> None:
        """供原生运行时回调，表示某段音频已经开始。"""

        event = {
            "reply_id": str(reply_id or "").strip(),
            "segment_index": int(segment_index),
            "started_at_ms": int(started_at_ms or time.time() * 1000),
            "server_received_at_ms": int(time.time() * 1000),
        }
        if not event["reply_id"]:
            return
        self._audio_start_events[event["reply_id"]] = event
        waiter = self._audio_start_waiters.pop(event["reply_id"], None)
        if waiter is not None and not waiter.done():
            loop = self._event_loop
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(waiter.set_result, dict(event))
            else:
                waiter.set_result(dict(event))

    def _log_info(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)


def _plugin_data_dir() -> Path:
    return Path(__file__).resolve().parent / "data"


def _punctuation_pause_ms(char: str) -> int:
    if char in ",，、":
        return 120
    if char in ".。!?！？；:：":
        return 250
    return 0
