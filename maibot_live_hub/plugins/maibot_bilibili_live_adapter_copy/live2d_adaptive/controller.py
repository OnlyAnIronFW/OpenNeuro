"""Adaptive Live2D controller with ordered delivery and synchronized speech timelines."""

from __future__ import annotations

from typing import Any, Mapping

import asyncio
import contextlib
from uuid import uuid4

from .bridge import Live2DBridgeProtocol
from .profile import ParameterProfile
from .semantic_mapper import Live2DSemanticMapper
from .speech_timeline import SpeechTimeline, SpeechTimelineBuilder

LIP_SYNC_ONLY_ROLES = frozenset({"mouth.open", "mouth.form"})
DEFAULT_IDLE_MOTION_MODEL = "hiyori"
DEFAULT_IDLE_MOTION_NAME = "m01"
DEFAULT_IDLE_MOTION_FILE = "m01.motion3.json"


class Live2DController:
    """High-level Live2D controller used by the Bilibili live adapter."""

    def __init__(
        self,
        *,
        bridge: Live2DBridgeProtocol,
        profile: ParameterProfile,
        chars_per_second: float = 7.5,
        prepare_ms: int = 180,
        release_ms: int = 600,
        mouth_update_interval_ms: int = 80,
        mouth_closed_value: float = 0.0,
        mouth_open_threshold: float = 0.08,
        mouth_open_gamma: float = 1.45,
        mouth_open_gain: float = 1.15,
        mouth_open_max: float = 0.88,
        mouth_sync_mode: str = "hybrid",
        mouth_amplitude_mix: float = 0.65,
        mouth_viseme_lead_ms: int = 40,
        mouth_open_smoothing: float = 0.55,
        mouth_open_attack_smoothing: float | None = None,
        mouth_open_release_smoothing: float | None = None,
        mouth_open_min_delta: float = 0.04,
        mouth_form_smoothing: float = 0.40,
        mouth_form_min_delta: float = 0.03,
        mouth_keyframe_transition_ms: int = 100,
        mouth_vowel_shapes: Mapping[str, Any] | None = None,
        parameter_keepalive_ms: int = 650,
        lip_sync_only_mode: bool = False,
        idle_motion_enabled: bool = False,
        idle_motion_model: str = DEFAULT_IDLE_MOTION_MODEL,
        idle_motion_name: str = DEFAULT_IDLE_MOTION_NAME,
        idle_motion_file: str = DEFAULT_IDLE_MOTION_FILE,
        idle_motion_interval_ms: int = 9000,
        idle_sway_enabled: bool = True,
        idle_sway_interval_ms: int = 900,
        idle_sway_intensity: float = 0.25,
        speech_sway_enabled: bool = True,
        speech_sway_intensity: float = 0.45,
        speech_sway_update_interval_ms: int = 160,
        logger: Any = None,
    ) -> None:
        self.bridge = bridge
        self.profile = profile
        self.mapper = Live2DSemanticMapper(profile)
        self.lip_sync_only_mode = bool(lip_sync_only_mode)
        self.mouth_sync_mode = str(mouth_sync_mode or "").strip().lower().replace("-", "_")
        self.use_vts_native_lip_sync = self.mouth_sync_mode == "vts_native"
        self.idle_motion_enabled = bool(idle_motion_enabled)
        self.idle_motion_model = _normalize_motion_text(idle_motion_model, DEFAULT_IDLE_MOTION_MODEL)
        self.idle_motion_name = _normalize_motion_text(idle_motion_name, DEFAULT_IDLE_MOTION_NAME)
        self.idle_motion_file = _normalize_motion_file(idle_motion_file, self.idle_motion_name)
        self.idle_motion_interval_ms = max(120, int(idle_motion_interval_ms))
        self.timeline_builder = SpeechTimelineBuilder(
            self.mapper,
            chars_per_second=chars_per_second,
            prepare_ms=prepare_ms,
            release_ms=release_ms,
            mouth_update_interval_ms=mouth_update_interval_ms,
            mouth_closed_value=mouth_closed_value,
            mouth_open_threshold=mouth_open_threshold,
            mouth_open_gamma=mouth_open_gamma,
            mouth_open_gain=mouth_open_gain,
            mouth_open_max=mouth_open_max,
            mouth_sync_mode=mouth_sync_mode,
            mouth_amplitude_mix=mouth_amplitude_mix,
            mouth_viseme_lead_ms=mouth_viseme_lead_ms,
            mouth_open_smoothing=mouth_open_smoothing,
            mouth_open_attack_smoothing=mouth_open_attack_smoothing,
            mouth_open_release_smoothing=mouth_open_release_smoothing,
            mouth_open_min_delta=mouth_open_min_delta,
            mouth_form_smoothing=mouth_form_smoothing,
            mouth_form_min_delta=mouth_form_min_delta,
            mouth_keyframe_transition_ms=mouth_keyframe_transition_ms,
            mouth_vowel_shapes=mouth_vowel_shapes,
            speech_sway_enabled=speech_sway_enabled and not self.lip_sync_only_mode and not self.idle_motion_enabled,
            speech_sway_intensity=speech_sway_intensity,
            speech_sway_update_interval_ms=speech_sway_update_interval_ms,
        )
        self.parameter_keepalive_ms = max(100, int(parameter_keepalive_ms))
        self.idle_sway_enabled = bool(idle_sway_enabled) and not self.lip_sync_only_mode and not self.idle_motion_enabled
        self.idle_sway_interval_ms = max(120, int(idle_sway_interval_ms))
        self.idle_sway_intensity = min(1.0, max(0.0, float(idle_sway_intensity)))
        self.speech_sway_intensity = min(1.0, max(0.0, float(speech_sway_intensity)))
        self.logger = logger
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._idle_motion_task: asyncio.Task[None] | None = None
        self._idle_sway_task: asyncio.Task[None] | None = None
        self._speaking_release_task: asyncio.Task[None] | None = None
        self._speaking = False
        self._active_timeline_id = ""
        self._idle_phase = 0.0

    @property
    def is_speaking(self) -> bool:
        """Return whether a synchronized reply timeline is active."""

        return self._speaking

    @property
    def active_timeline_id(self) -> str:
        """Return the current active speech timeline id."""

        return self._active_timeline_id

    async def start(self) -> None:
        """Start bridge and ordered delivery worker."""

        await self.bridge.start()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._worker_loop(), name="live2d_adaptive.controller")
        if self.idle_motion_enabled:
            await self._queue_idle_motion()
            if self._idle_motion_task is None or self._idle_motion_task.done():
                self._idle_motion_task = asyncio.create_task(
                    self._idle_motion_loop(),
                    name="live2d_adaptive.idle_motion",
                )
        if self.idle_sway_enabled and (self._idle_sway_task is None or self._idle_sway_task.done()):
            self._idle_sway_task = asyncio.create_task(
                self._idle_sway_loop(),
                name="live2d_adaptive.idle_sway",
            )

    async def stop(self) -> None:
        """Stop worker and bridge."""

        if self._idle_motion_task is not None:
            self._idle_motion_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_motion_task
        self._idle_motion_task = None
        if self._idle_sway_task is not None:
            self._idle_sway_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_sway_task
        self._idle_sway_task = None
        await self._queue.put(None)
        worker = self._worker
        self._worker = None
        if worker is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        if self._speaking_release_task is not None:
            self._speaking_release_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._speaking_release_task
        self._speaking_release_task = None
        await self.bridge.stop()
        self._speaking = False
        self._active_timeline_id = ""

    async def send_parameters(
        self,
        parameters: list[Mapping[str, Any]],
        *,
        duration_ms: int = 300,
        easing: str = "easeOutQuad",
        blend: str = "replace",
        priority: int = 5,
        timeline_id: str = "",
    ) -> dict[str, Any]:
        """Queue a raw parameter batch after validation and clamping."""

        normalized_parameters = self._normalize_parameters(parameters)
        if not normalized_parameters:
            return {"success": False, "error": "no valid Live2D parameters"}
        event = {
            "type": "live2d.parameters",
            "timeline_id": timeline_id or uuid4().hex,
            "parameters": normalized_parameters,
            "duration_ms": max(0, int(duration_ms)),
            "easing": _normalize_easing(easing),
            "blend": _normalize_blend(blend),
            "priority": int(priority),
        }
        await self._queue_parameter_with_keepalive(event)
        return {"success": True, "parameters": normalized_parameters, "event": event}

    async def send_intent(
        self,
        intent: str,
        *,
        intensity: float = 0.6,
        target: Mapping[str, Any] | None = None,
        duration_ms: int = 600,
    ) -> dict[str, Any]:
        """Queue a high-level Live2D intent."""

        if self.lip_sync_only_mode and str(intent or "").strip().lower() != "speak":
            return {"success": False, "error": "Live2D lip-sync-only mode blocks non-speech intents"}
        parameters = self.mapper.build_intent(intent, intensity=intensity, target=target or {})
        if not parameters:
            return {"success": False, "error": f"intent produced no controllable parameters: {intent}"}
        return await self.send_parameters(parameters, duration_ms=duration_ms, priority=5)

    async def play_reply(
        self,
        text: str,
        *,
        audio_timeline: Mapping[str, Any] | None = None,
        emotion_intent: str = "",
        motion_intensity: float | None = None,
        timeline_prepare_ms: int | None = None,
    ) -> SpeechTimeline:
        """Queue a reply timeline synchronized with text or future TTS metadata."""

        resolved_motion_intensity = self._resolve_speech_motion_intensity(
            text,
            emotion_intent=emotion_intent,
            motion_intensity=motion_intensity,
        )
        timeline = self.timeline_builder.build_reply_timeline(
            text,
            audio_timeline=audio_timeline,
            motion_intensity=resolved_motion_intensity,
            prepare_ms_override=timeline_prepare_ms,
        )
        self._speaking = True
        self._active_timeline_id = timeline.timeline_id
        self._schedule_speaking_release(timeline)
        if emotion_intent and not self.lip_sync_only_mode and not self.idle_motion_enabled:
            emotion_parameters = self.mapper.build_intent(emotion_intent, intensity=0.55, target={"text": text})
            if emotion_parameters:
                await self._queue_parameter_with_keepalive(
                    {
                        "type": "live2d.parameters",
                        "timeline_id": timeline.timeline_id,
                        "parameters": emotion_parameters,
                        "duration_ms": 240,
                        "easing": "easeOutQuad",
                        "blend": "replace",
                        "priority": 4,
                    }
                )
        for event in timeline.events:
            sanitized_event = self._sanitize_bridge_event(event.to_bridge_payload())
            if sanitized_event is not None:
                await self._queue.put(sanitized_event)
        return timeline

    async def _idle_motion_loop(self) -> None:
        interval_sec = self.idle_motion_interval_ms / 1000.0
        while True:
            await asyncio.sleep(interval_sec)
            await self._queue_idle_motion()

    async def _queue_idle_motion(self) -> None:
        await self._queue.put(
            {
                "type": "live2d.motion",
                "timeline_id": "idle-motion",
                "model": self.idle_motion_model,
                "motion": self.idle_motion_name,
                "motion_file": self.idle_motion_file,
                "loop": True,
                "priority": 1,
                "purpose": "idle",
            }
        )

    async def _idle_sway_loop(self) -> None:
        interval_sec = self.idle_sway_interval_ms / 1000.0
        while True:
            await asyncio.sleep(interval_sec)
            if self._speaking:
                continue
            parameters = self.mapper.build_sway(
                self._idle_phase,
                intensity=self.idle_sway_intensity,
                speaking=False,
            )
            self._idle_phase = (self._idle_phase + 0.58) % 6.283185307179586
            normalized_parameters = self._normalize_parameters(parameters)
            if not normalized_parameters:
                continue
            await self._queue_parameter_with_keepalive(
                {
                    "type": "live2d.parameters",
                    "timeline_id": "idle-sway",
                    "parameters": normalized_parameters,
                    "duration_ms": self.idle_sway_interval_ms + 180,
                    "easing": "easeInOut",
                    "blend": "replace",
                    "priority": 1,
                    "idle": True,
                }
            )

    async def _worker_loop(self) -> None:
        while True:
            event = await self._queue.get()
            if event is None:
                self._queue.task_done()
                break
            try:
                sanitized_event = self._sanitize_bridge_event(event)
                if sanitized_event is None:
                    continue
                response = await self.bridge.send_event(sanitized_event)
                self._handle_bridge_response(sanitized_event, response)
            except Exception as exc:
                self._log_warning(f"Live2D bridge event failed: {exc}")
            finally:
                self._queue.task_done()

    async def _queue_parameter_with_keepalive(self, event: dict[str, Any]) -> None:
        sanitized_event = self._sanitize_bridge_event(event)
        if sanitized_event is None:
            return
        await self._queue.put(sanitized_event)
        duration_ms = int(sanitized_event.get("duration_ms") or 0)
        if duration_ms <= self.parameter_keepalive_ms:
            return
        offset = self.parameter_keepalive_ms
        while offset < duration_ms:
            keepalive_event = dict(sanitized_event)
            keepalive_event["offset_ms"] = offset
            keepalive_event["keepalive"] = True
            keepalive_event["duration_ms"] = min(self.parameter_keepalive_ms, duration_ms - offset)
            await self._queue.put(keepalive_event)
            offset += self.parameter_keepalive_ms

    def _schedule_speaking_release(self, timeline: SpeechTimeline) -> None:
        if self._speaking_release_task is not None:
            self._speaking_release_task.cancel()
        self._speaking_release_task = asyncio.create_task(
            self._release_speaking_after(timeline.timeline_id, timeline.estimated_duration_ms + timeline.release_ms),
            name="live2d_adaptive.speaking_release",
        )

    async def _release_speaking_after(self, timeline_id: str, delay_ms: int) -> None:
        await asyncio.sleep(max(0.0, delay_ms / 1000.0))
        if self._active_timeline_id == timeline_id:
            self._speaking = False
            self._active_timeline_id = ""

    def _resolve_speech_motion_intensity(
        self,
        text: str,
        *,
        emotion_intent: str,
        motion_intensity: float | None,
    ) -> float:
        base = self.speech_sway_intensity if motion_intensity is None else float(motion_intensity)
        strong_marks = {"!", "?", "\uff01", "\uff1f"}
        punctuation_boost = min(0.24, sum(1 for char in text if char in strong_marks) * 0.06)
        emotion_boosts = {
            "react_happy": 0.10,
            "react_surprised": 0.24,
            "react_confused": 0.12,
            "react_emphasis": 0.18,
            "react_shy": 0.04,
        }
        emotion_boost = emotion_boosts.get(str(emotion_intent or "").strip(), 0.0)
        return min(1.0, max(0.0, base + punctuation_boost + emotion_boost))

    def _normalize_parameters(self, parameters: list[Mapping[str, Any]]) -> list[dict[str, float | str]]:
        normalized: list[dict[str, float | str]] = []
        for raw_parameter in parameters:
            if not isinstance(raw_parameter, Mapping):
                continue
            parameter_id = str(raw_parameter.get("id") or "").strip()
            spec = self.profile.parameters.get(parameter_id)
            if spec is None or not spec.enabled or not self._is_parameter_allowed(spec.role):
                continue
            try:
                value = float(raw_parameter.get("value"))
            except (TypeError, ValueError):
                continue
            weight = raw_parameter.get("weight", 0.7)
            try:
                normalized_weight = min(1.0, max(0.0, float(weight)))
            except (TypeError, ValueError):
                normalized_weight = 0.7
            normalized.append(
                {
                    "id": parameter_id,
                    "value": spec.clamp(value),
                    "weight": normalized_weight,
                }
            )
        return normalized

    def _sanitize_bridge_event(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        event_type = str(event.get("type") or "").strip()
        if event_type not in {"live2d.parameters", "live2d.timeline.frame"}:
            return dict(event)
        raw_parameters = event.get("parameters")
        if not isinstance(raw_parameters, list):
            return None
        normalized_parameters = self._normalize_parameters(raw_parameters)
        if not normalized_parameters:
            return None
        sanitized_event = dict(event)
        sanitized_event["parameters"] = normalized_parameters
        return sanitized_event

    def _is_parameter_allowed(self, role: str) -> bool:
        normalized_role = str(role or "").strip()
        if self.use_vts_native_lip_sync and normalized_role.startswith("mouth."):
            return False
        if not self.lip_sync_only_mode:
            return True
        return normalized_role in LIP_SYNC_ONLY_ROLES

    def _handle_bridge_response(self, event: Mapping[str, Any], response: Any) -> None:
        if not isinstance(response, Mapping):
            return
        error = str(response.get("error") or response.get("code") or "").lower()
        if "unknown_parameter" not in error and "out_of_range" not in error:
            return
        for raw_parameter in event.get("parameters", []):
            if isinstance(raw_parameter, Mapping):
                parameter_id = str(raw_parameter.get("id") or "").strip()
                if parameter_id:
                    self.profile.mark_failed(parameter_id)

    def _log_warning(self, message: str) -> None:
        if self.logger is not None:
            self.logger.warning(message)


def _normalize_easing(value: str) -> str:
    normalized = str(value or "").strip()
    allowed = {"linear", "easeIn", "easeOut", "easeInOut", "easeOutQuad"}
    return normalized if normalized in allowed else "linear"


def _normalize_blend(value: str) -> str:
    normalized = str(value or "").strip()
    allowed = {"replace", "additive", "multiply"}
    return normalized if normalized in allowed else "replace"


def _normalize_motion_text(value: str, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def _normalize_motion_file(value: str, motion_name: str) -> str:
    normalized = _normalize_motion_text(value, "")
    if normalized:
        return normalized
    return f"{motion_name}.motion3.json" if motion_name else DEFAULT_IDLE_MOTION_FILE
