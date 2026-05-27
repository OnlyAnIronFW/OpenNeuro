"""MaiBot Bilibili live adapter plugin."""

from __future__ import annotations

import asyncio
import hashlib
import wave

from pathlib import Path
from typing import Any, Callable, ClassVar, Mapping, cast

from uuid import uuid4

from maibot_sdk import API, HookHandler, MaiBotPlugin, MessageGateway, PluginConfigBase, Tool
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

from .bilibili_transport import BilibiliDanmakuTransport
from .bridge_client import JsonBridgeClient
from .audio_output import LocalAudioOutputPlayer
from .config import LiveAdapterSettings
from .constants import DEFAULT_VTS_WS_URL, GATEWAY_NAME, PLATFORM_NAME, PROTOCOL_NAME
from .event_router import LiveEventRouter
from .interaction_planner import LiveInteractionPlanner
from .local_voice_controller import LocalVoiceController
from .live2d_adaptive import CapabilityProbe, JsonLive2DBridge, Live2DController
from .live2d_adaptive.speech_timeline import build_text_viseme_timeline, split_text_segments
from .message_codec import build_local_voice_message_dict, extract_live_output_text_from_message
from .netease_client import NeteaseCloudMusicClient
from .runtime_state import LiveAdapterRuntimeState
from .rvc_song_pipeline import RvcSongPipeline
from .song_request_console import SongRequestConsoleSession
from .song_request_service import RvcSongRequestService
from .sts2_controller import STS2Controller
from .sts2_llm_client import STS2DecisionClient
from .sts2_logging import STS2LogSession
from .sts2_mcp_client import STS2MCPClient
from .subtitle_webui import SubtitleSegment, SubtitleWebUIService, estimate_subtitle_duration_ms
from .translation_client import SubtitleTranslationClient
from .tts_provider import (
    GPTSoVITSTTSProvider,
    SynthesizedSpeech,
    TTSProviderProtocol,
    build_synthesized_speech_from_wav,
)


class BilibiliLiveAdapterPlugin(MaiBotPlugin):
    """Input-only Bilibili live adapter with synchronized Live2D/Game output."""

    config_model: ClassVar[type[PluginConfigBase] | None] = LiveAdapterSettings

    def __init__(self) -> None:
        super().__init__()
        self._runtime_state: LiveAdapterRuntimeState | None = None
        self._planner: LiveInteractionPlanner | None = None
        self._router: LiveEventRouter | None = None
        self._transport: BilibiliDanmakuTransport | None = None
        self._live2d_controller: Live2DController | None = None
        self._game_bridge: JsonBridgeClient | None = None
        self._sts2_controller: STS2Controller | None = None
        self._sts2_log_session: STS2LogSession | None = None
        self._tts_provider: TTSProviderProtocol | None = None
        self._audio_output_player: LocalAudioOutputPlayer | None = None
        self._subtitle_webui: SubtitleWebUIService | None = None
        self._subtitle_translator: Any | None = None
        self._song_request_service: RvcSongRequestService | None = None
        self._song_request_console_session: SongRequestConsoleSession | None = None
        self._local_voice_controller: LocalVoiceController | None = None
        self._napcat_disabled_for_live = False

    async def on_load(self) -> None:
        """Start configured bridges and the Bilibili input transport."""

        await self._restart_runtime()

    async def on_unload(self) -> None:
        """Stop all runtime components."""

        await self._stop_runtime()

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        """Reload plugin config and restart runtime if the plugin config changed."""

        if scope != "self":
            return
        normalized_settings = LiveAdapterSettings.model_validate(config_data)
        current_settings = self._load_settings()
        normalized_config = normalized_settings.model_dump(mode="python")
        if normalized_config == current_settings.model_dump(mode="python"):
            self.set_plugin_config(normalized_config)
            if version:
                self._logger().debug(
                    "Bilibili live adapter config update matches in-memory state; "
                    f"skip restart for version={version}"
                )
            return
        self.set_plugin_config(normalized_config)
        if version:
            self._logger().debug(f"Bilibili live adapter config update received: {version}")
        await self._restart_runtime()

    @MessageGateway(
        name=GATEWAY_NAME,
        route_type="duplex",
        platform=PLATFORM_NAME,
        protocol=PROTOCOL_NAME,
        description="Bilibili live duplex gateway; outbound replies drive Live2D/Game only.",
    )
    async def handle_bilibili_gateway(
        self,
        message: dict[str, Any],
        route: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Handle MaiBot outbound messages without sending them back to Bilibili."""

        del route
        text = extract_live_output_text_from_message(message)
        if not text:
            return {"success": False, "error": "empty outbound message"}
        settings = self._load_settings()
        render_metadata = _build_render_metadata(message, metadata)
        render_result = await self._render_local_reply(
            text,
            settings=settings,
            source_platform=PLATFORM_NAME,
            metadata=render_metadata,
            kwargs=kwargs,
            on_audio_start=self._build_sts2_audio_start_callback(PLATFORM_NAME, render_metadata),
        )

        return {
            "success": True,
            "external_message_id": f"bilibili-live-local-{uuid4().hex}",
            "metadata": render_result,
        }

    @HookHandler(
        "send_service.before_send",
        name="live2d_mirror_before_send",
        description="Mirror ordinary MaiBot outbound replies to Live2D before platform delivery.",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        timeout_ms=30000,
        error_policy=ErrorPolicy.LOG,
    )
    async def mirror_outbound_reply_to_live2d(
        self,
        message: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Mirror non-live-gateway outbound replies so QQ tests also drive Live2D."""
        song_service = self._song_request_service
        if song_service is not None and song_service.is_playback_active:
            return {
                "success": True,
                "action": "abort",
                "custom_result": {"treat_as_sent": True, "song_playback_suppressed": True},
            }
        if not isinstance(message, Mapping):
            return {"success": True, "action": "continue"}
        settings = self._load_settings()
        should_mirror_live2d = bool(
            settings.live2d.enabled
            and settings.live2d.send_bot_replies
            and settings.live2d.mirror_other_platform_replies
            and self._live2d_controller is not None
        )
        should_publish_webui = bool(settings.webui.enabled and self._subtitle_webui is not None)
        if not settings.plugin.enabled or (not should_mirror_live2d and not should_publish_webui):
            return {"success": True, "action": "continue"}
        use_vts_native_lip_sync = _uses_vts_native_lip_sync(settings)
        platform = _extract_platform(message)
        modified_kwargs = _build_local_render_only_modified_kwargs(kwargs) if platform == PLATFORM_NAME else None
        if platform == PLATFORM_NAME:
            text = extract_live_output_text_from_message(message)
            if not text:
                return {
                    "success": True,
                    "action": "abort",
                    "modified_kwargs": modified_kwargs,
                    "custom_result": {"treat_as_sent": True},
                }
            render_result = await self._render_local_reply(
                text,
                settings=settings,
                source_platform=platform,
                metadata=message,
                kwargs={},
                on_audio_start=self._build_sts2_audio_start_callback(platform, message),
            )
            return {
                "success": True,
                "action": "abort",
                "modified_kwargs": modified_kwargs,
                "custom_result": {
                    "treat_as_sent": True,
                    **render_result,
                },
            }
        text = extract_live_output_text_from_message(message)
        if not text:
            return {"success": True, "action": "continue"}
        speech_text, subtitle_text, audio_timeline, synthesized_speech = await self._prepare_reply_delivery(
            text,
            settings=settings,
            metadata=message,
            kwargs={},
        )
        webui_published, webui_audio_started = await self._publish_reply_to_webui(
            subtitle_text,
            settings=settings,
            source_platform=platform,
            speech_text=_webui_original_text_for_subtitle(speech_text, subtitle_text, settings),
            audio_timeline=audio_timeline,
            synthesized_speech=synthesized_speech,
            wait_for_audio_start=should_mirror_live2d and not use_vts_native_lip_sync,
            on_audio_start=self._build_sts2_audio_start_callback(platform, message),
        )
        timeline = None
        if should_mirror_live2d:
            play_kwargs: dict[str, Any] = {}
            if webui_audio_started:
                play_kwargs["timeline_prepare_ms"] = 0
            timeline = await self._live2d_controller.play_reply(
                speech_text,
                audio_timeline=audio_timeline,
                emotion_intent=_infer_emotion_intent(speech_text),
                **play_kwargs,
            )
        audio_playback_task = self._create_audio_playback_task(
            synthesized_speech,
            settings=settings,
            enabled=use_vts_native_lip_sync,
            on_audio_start=self._build_sts2_audio_start_callback(platform, message),
        )
        audio_played_to_vts = await audio_playback_task if audio_playback_task is not None else False
        if timeline is not None:
            self._logger().info(
                "Mirrored outbound reply to Live2D: "
                f"platform={platform or 'unknown'} timeline_id={timeline.timeline_id} text={text[:40]!r}"
            )
        return {
            "success": True,
            "action": "continue",
            "custom_result": {
                "live2d_synchronized": timeline is not None,
                "timeline_id": timeline.timeline_id if timeline is not None else "",
                "platform": platform,
                "audio_ref": synthesized_speech.audio_ref if synthesized_speech is not None else "",
                "webui_published": webui_published,
                "webui_audio_started": webui_audio_started,
                "audio_played_to_vts": audio_played_to_vts,
                "language_mode": settings.language.mode,
                "speech_text": speech_text,
                "subtitle_text": subtitle_text,
            },
        }

    @HookHandler(
        "maisaka.planner.before_request",
        name="bilibili_live_language_before_request",
        description="Force Bilibili live replies to English spoken text in bilingual mode.",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        timeout_ms=1000,
        error_policy=ErrorPolicy.LOG,
    )
    async def enforce_live_language_prompt(
        self,
        messages: list[dict[str, Any]] | None = None,
        tool_definitions: list[dict[str, Any]] | None = None,
        session_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Append the English voice instruction only to the Bilibili live planner request."""

        del kwargs
        normalized_messages = [dict(message) for message in (messages or []) if isinstance(message, Mapping)]
        normalized_tools = list(tool_definitions or [])
        settings = self._load_settings()
        is_live_session = settings.plugin.enabled and str(session_id or "").strip() == _build_live_chat_id(settings)
        if is_live_session:
            normalized_tools = _remove_finish_tool_definitions(normalized_tools)
        if not is_live_session or not settings.language.is_english_voice_chinese_subtitle():
            return {"messages": normalized_messages, "tool_definitions": normalized_tools}
        language_prompt = settings.language.english_system_prompt.strip()
        if not language_prompt:
            return {"messages": normalized_messages, "tool_definitions": normalized_tools}
        return {
            "messages": _append_language_prompt(normalized_messages, language_prompt),
            "tool_definitions": normalized_tools,
        }

    async def _render_local_reply(
        self,
        text: str,
        *,
        settings: LiveAdapterSettings,
        source_platform: str,
        metadata: Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
        on_audio_start: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        timeline_id = _extract_timeline_id(metadata)
        speech_text, subtitle_text, audio_timeline, synthesized_speech = await self._prepare_reply_delivery(
            text,
            settings=settings,
            metadata=metadata,
            kwargs=kwargs,
        )
        use_vts_native_lip_sync = _uses_vts_native_lip_sync(settings)

        should_play_live2d = bool(
            settings.live2d.enabled and settings.live2d.send_bot_replies and self._live2d_controller is not None
        )
        webui_published, webui_audio_started = await self._publish_reply_to_webui(
            subtitle_text,
            settings=settings,
            source_platform=source_platform,
            speech_text=_webui_original_text_for_subtitle(speech_text, subtitle_text, settings),
            audio_timeline=audio_timeline,
            synthesized_speech=synthesized_speech,
            wait_for_audio_start=should_play_live2d and not use_vts_native_lip_sync,
            on_audio_start=on_audio_start if not use_vts_native_lip_sync else None,
        )
        timeline = None
        if should_play_live2d:
            play_kwargs: dict[str, Any] = {}
            if webui_audio_started:
                play_kwargs["timeline_prepare_ms"] = 0
            timeline = await self._live2d_controller.play_reply(
                speech_text,
                audio_timeline=audio_timeline,
                emotion_intent=_infer_emotion_intent(speech_text),
                **play_kwargs,
            )
        audio_playback_task = self._create_audio_playback_task(
            synthesized_speech,
            settings=settings,
            enabled=use_vts_native_lip_sync,
            on_audio_start=on_audio_start if use_vts_native_lip_sync else None,
        )
        audio_played_to_vts = await audio_playback_task if audio_playback_task is not None else False
        return {
            "bilibili_sent": False,
            "timeline_id": timeline.timeline_id if timeline is not None else timeline_id,
            "live2d_synchronized": timeline is not None,
            "audio_ref": synthesized_speech.audio_ref if synthesized_speech is not None else "",
            "audio_duration_ms": synthesized_speech.audio_duration_ms if synthesized_speech is not None else 0,
            "webui_published": webui_published,
            "webui_audio_started": webui_audio_started,
            "audio_played_to_vts": audio_played_to_vts,
            "local_delivery": True,
            "language_mode": settings.language.mode,
            "speech_text": speech_text,
            "subtitle_text": subtitle_text,
        }

    async def _render_external_audio_reply(
        self,
        caption_text: str,
        synthesized_speech: SynthesizedSpeech,
        *,
        settings: LiveAdapterSettings,
        source_platform: str,
        metadata: Mapping[str, Any] | None,
        on_audio_start: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        timeline_id = _extract_timeline_id(metadata)
        audio_timeline = synthesized_speech.to_audio_timeline()
        if _uses_vts_native_lip_sync(settings):
            audio_timeline.pop("visemes", None)
        else:
            audio_timeline = _with_text_visemes(audio_timeline, caption_text, settings=settings)
        use_vts_native_lip_sync = _uses_vts_native_lip_sync(settings)
        should_play_live2d = bool(
            settings.live2d.enabled and settings.live2d.send_bot_replies and self._live2d_controller is not None
        )
        webui_published, webui_audio_started = await self._publish_reply_to_webui(
            caption_text,
            settings=settings,
            source_platform=source_platform,
            speech_text="",
            audio_timeline=audio_timeline,
            synthesized_speech=synthesized_speech,
            wait_for_audio_start=should_play_live2d and not use_vts_native_lip_sync,
            on_audio_start=on_audio_start if not use_vts_native_lip_sync else None,
        )
        timeline = None
        if should_play_live2d:
            play_kwargs: dict[str, Any] = {}
            if webui_audio_started:
                play_kwargs["timeline_prepare_ms"] = 0
            timeline = await self._live2d_controller.play_reply(
                caption_text,
                audio_timeline=audio_timeline,
                emotion_intent="",
                **play_kwargs,
            )
        audio_playback_task = self._create_audio_playback_task(
            synthesized_speech,
            settings=settings,
            enabled=use_vts_native_lip_sync,
            on_audio_start=on_audio_start if use_vts_native_lip_sync else None,
        )
        audio_played_to_vts = await audio_playback_task if audio_playback_task is not None else False
        if audio_playback_task is None and synthesized_speech.audio_duration_ms > 0:
            await asyncio.sleep(max(0.0, synthesized_speech.audio_duration_ms / 1000.0))
        return {
            "bilibili_sent": False,
            "timeline_id": timeline.timeline_id if timeline is not None else timeline_id,
            "live2d_synchronized": timeline is not None,
            "audio_ref": synthesized_speech.audio_ref,
            "audio_duration_ms": synthesized_speech.audio_duration_ms,
            "webui_published": webui_published,
            "webui_audio_started": webui_audio_started,
            "audio_played_to_vts": audio_played_to_vts,
            "local_delivery": True,
            "rvc_song": True,
        }

    @Tool(
        "control_live2d_parameters",
        description="Send raw Live2D parameter values through the adaptive parameter controller.",
        parameters={
            "parameters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "value": {"type": "number"},
                        "weight": {"type": "number"},
                    },
                    "required": ["id", "value"],
                },
                "required": True,
            },
            "duration_ms": {"type": "integer", "default": 300},
            "easing": {"type": "string", "default": "easeOutQuad"},
            "blend": {"type": "string", "default": "replace"},
            "priority": {"type": "integer", "default": 5},
        },
    )
    async def control_live2d_parameters(
        self,
        parameters: list[Mapping[str, Any]],
        duration_ms: int = 300,
        easing: str = "easeOutQuad",
        blend: str = "replace",
        priority: int = 5,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Tool handler for raw Live2D parameter control."""

        del kwargs
        controller = self._live2d_controller
        if controller is None:
            return {"success": False, "error": "Live2D controller is not enabled"}
        return await controller.send_parameters(
            list(parameters or []),
            duration_ms=duration_ms,
            easing=easing,
            blend=blend,
            priority=priority,
        )

    @Tool(
        "control_live2d_intent",
        description="Drive a high-level Live2D semantic intent using discovered model parameters.",
        parameters={
            "intent": {"type": "string", "required": True},
            "intensity": {"type": "number", "default": 0.6},
            "target": {"type": "object", "default": {}},
            "duration_ms": {"type": "integer", "default": 600},
        },
    )
    async def control_live2d_intent(
        self,
        intent: str,
        intensity: float = 0.6,
        target: Mapping[str, Any] | None = None,
        duration_ms: int = 600,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Tool handler for semantic Live2D intents."""

        del kwargs
        controller = self._live2d_controller
        if controller is None:
            return {"success": False, "error": "Live2D controller is not enabled"}
        normalized_intent = str(intent or "").strip()
        target_payload = dict(target or {})
        settings = self._load_settings()
        if normalized_intent == "speak":
            speech_text = str(target_payload.get("text") or "").strip()
            if speech_text:
                synthesized_speech = await self._synthesize_reply_speech(speech_text, settings=settings)
                audio_timeline = (
                    synthesized_speech.to_audio_timeline()
                    if synthesized_speech is not None
                    else {"audio_duration_ms": max(1, int(duration_ms))}
                )
                timeline = await controller.play_reply(
                    speech_text,
                    audio_timeline=audio_timeline,
                    emotion_intent="",
                    motion_intensity=float(intensity),
                )
                return {
                    "success": True,
                    "intent": normalized_intent,
                    "timeline_id": timeline.timeline_id,
                    "estimated_duration_ms": timeline.estimated_duration_ms,
                    "events": len(timeline.events),
                    "audio_ref": synthesized_speech.audio_ref if synthesized_speech is not None else "",
                }
        return await controller.send_intent(
            normalized_intent,
            intensity=float(intensity),
            target=target_payload,
            duration_ms=int(duration_ms),
        )

    @Tool(
        "control_game",
        description="Forward a validated JSON action to the external game bridge.",
        parameters={
            "action": {"type": "string", "required": True},
            "payload": {"type": "object", "required": True},
            "stream_id": {"type": "string", "default": ""},
        },
    )
    async def control_game(
        self,
        action: str,
        payload: Mapping[str, Any],
        stream_id: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Tool handler for game JSON bridge commands."""

        del kwargs
        settings = self._load_settings()
        normalized_action = str(action or "").strip()
        if not normalized_action:
            return {"success": False, "error": "action is required"}
        allowed_actions = set(settings.game.allowed_actions)
        if allowed_actions and normalized_action not in allowed_actions:
            return {"success": False, "error": f"game action is not allowed: {normalized_action}"}
        if not settings.game.enabled or self._game_bridge is None:
            return {"success": False, "error": "game bridge is not enabled"}
        return await self._game_bridge.send(
            "game_command",
            {"action": normalized_action, "payload": dict(payload or {}), "stream_id": str(stream_id or "")},
        )

    @Tool(
        "request_rvc_song",
        description="Queue a Netease song request and convert it through RVC for live playback.",
        parameters={
            "song_keyword": {"type": "string", "required": True},
            "stream_id": {"type": "string", "default": ""},
            "requester": {"type": "string", "default": ""},
            "artist": {"type": "string", "default": ""},
        },
    )
    async def request_rvc_song(
        self,
        song_keyword: str,
        stream_id: str = "",
        requester: str = "",
        artist: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Tool handler for RVC song requests."""

        del kwargs
        settings = self._load_settings()
        service = self._song_request_service
        if service is None:
            if settings.song_request.hard_disable:
                prompt = "\u70b9\u6b4c\u548cRVC\u529f\u80fd\u5f53\u524d\u88ab\u603b\u5f00\u5173\u7981\u7528\u4e86\u3002"
                return {"success": False, "queued": False, "prompt": prompt, "message": prompt}
            if not settings.song_request.enabled:
                prompt = "\u70b9\u6b4c\u529f\u80fd\u8fd8\u6ca1\u6709\u5f00\u542f\u3002"
                return {"success": False, "queued": False, "prompt": prompt, "message": prompt}
            service = self._build_song_request_service(settings)
            self._song_request_service = service
            await service.start()
        return await service.submit(
            song_keyword=song_keyword,
            stream_id=stream_id,
            requester=requester,
            artist=artist,
        )

    @API("control_live2d_parameters", description="API wrapper for raw Live2D parameter control.", public=True)
    async def api_control_live2d_parameters(self, **kwargs: Any) -> dict[str, Any]:
        """API wrapper for other plugins."""

        return await self.control_live2d_parameters(**kwargs)

    @API("control_live2d_intent", description="API wrapper for Live2D intent control.", public=True)
    async def api_control_live2d_intent(self, **kwargs: Any) -> dict[str, Any]:
        """API wrapper for other plugins."""

        return await self.control_live2d_intent(**kwargs)

    @API("control_game", description="API wrapper for external game JSON bridge control.", public=True)
    async def api_control_game(self, **kwargs: Any) -> dict[str, Any]:
        """API wrapper for other plugins."""

        return await self.control_game(**kwargs)

    async def _restart_runtime(self) -> None:
        settings = self._load_settings()
        await self._stop_runtime()
        if self._runtime_state is None:
            self._runtime_state = self._create_runtime_state()

        if settings.live2d.enabled:
            self._live2d_controller = await self._build_live2d_controller(settings)
        if settings.game.enabled:
            self._game_bridge = JsonBridgeClient(
                name="game",
                http_url=settings.game.http_url,
                websocket_url=settings.game.websocket_url,
                auth_token=settings.game.auth_token,
                connect_timeout_sec=settings.game.connect_timeout_sec,
                logger=self._logger(),
            )
            await self._game_bridge.start()
        if settings.sts2.enabled:
            self._sts2_log_session = STS2LogSession(
                settings.sts2.logging,
                base_dir=_project_root(),
                parent_logger=self._logger(),
            ).start()
            sts2_logger: Any = self._sts2_log_session if settings.sts2.logging.enabled else self._logger()
            if self._sts2_log_session.log_path is not None:
                self._logger().info(f"STS2-Agent logs are written to {self._sts2_log_session.log_path}")
            self._sts2_controller = STS2Controller(
                gateway=self.ctx.gateway,
                settings=settings,
                mcp_client=STS2MCPClient(
                    settings.sts2.mcp,
                    logger=sts2_logger,
                    stderr=self._sts2_log_session.stderr if self._sts2_log_session is not None else None,
                ),
                decision_client=STS2DecisionClient(settings.sts2.llm, logger=sts2_logger),
                logger=sts2_logger,
            )
        if settings.language.is_english_voice_chinese_subtitle():
            self._subtitle_translator = SubtitleTranslationClient(settings.language.translation, logger=self._logger())
        if settings.tts.enabled and settings.tts.is_usable():
            self._tts_provider = await self._build_tts_provider(settings)
            if settings.tts.audio_playback_enabled:
                self._audio_output_player = LocalAudioOutputPlayer(
                    output_device=settings.tts.audio_output_device,
                    volume=settings.tts.audio_output_volume,
                    logger=self._logger(),
                )
        if settings.webui.enabled:
            await self._start_subtitle_webui(settings)
        if settings.song_request.is_available():
            self._song_request_service = self._build_song_request_service(settings)
            await self._song_request_service.start()

        self._planner = LiveInteractionPlanner(
            settings.interaction,
            llm=_ctx_attr(self, "llm"),
            logger=self._logger(),
            message_capability=_ctx_attr(self, "message"),
            chat_id=_build_live_chat_id(settings),
        )
        self._router = LiveEventRouter(
            gateway=self.ctx.gateway,
            settings=settings,
            planner=self._planner,
            live2d_controller=self._live2d_controller,
            game_bridge=self._game_bridge,
            sts2_controller=self._sts2_controller,
            logger=self._logger(),
        )

        if not settings.should_connect():
            self._logger().info("Bilibili live adapter is disabled; bridges and transport stay idle.")
            return
        self._local_voice_controller = LocalVoiceController(
            settings=settings,
            on_transcript_route=self._route_local_voice_text,
            on_settings_changed=self._handle_local_voice_settings_changed,
            logger=self._logger(),
        )
        await self._local_voice_controller.start()
        if not settings.validate_runtime_config(self._logger()):
            return

        self._transport = BilibiliDanmakuTransport(
            on_event=self._router.handle_event,
            on_connection_opened=lambda: self._handle_live_connection_opened(settings),
            on_connection_closed=lambda: self._handle_live_connection_closed(settings),
            logger=self._logger(),
        )
        self._transport.configure(settings.bilibili)
        await self._transport.start()

    def _create_runtime_state(self) -> LiveAdapterRuntimeState:
        return LiveAdapterRuntimeState(self.ctx.gateway, self._logger())

    async def _handle_live_connection_opened(self, settings: LiveAdapterSettings) -> None:
        await self._disable_napcat_for_live_if_needed(settings)
        if self._runtime_state is None:
            self._runtime_state = self._create_runtime_state()
        ready = await self._runtime_state.report_ready(settings)
        if ready and self._router is not None:
            self._router.start_idle_topic_watch()

    async def _handle_live_connection_closed(self, settings: LiveAdapterSettings) -> None:
        if self._router is not None:
            self._router.stop_idle_topic_watch()
        if self._runtime_state is not None:
            await self._runtime_state.report_disconnected()
        await self._restore_napcat_after_live_if_needed(settings)

    async def _disable_napcat_for_live_if_needed(self, settings: LiveAdapterSettings) -> None:
        if not settings.napcat.disable_on_live_connect:
            return
        disabled = await self._set_napcat_connection_enabled(
            settings,
            enabled=False,
            reason="bilibili_live_connected",
        )
        if disabled:
            self._napcat_disabled_for_live = True

    async def _restore_napcat_after_live_if_needed(self, settings: LiveAdapterSettings) -> None:
        if not (settings.napcat.restore_on_live_disconnect and self._napcat_disabled_for_live):
            return
        restored = await self._set_napcat_connection_enabled(
            settings,
            enabled=True,
            reason="bilibili_live_disconnected",
        )
        if restored:
            self._napcat_disabled_for_live = False

    async def _set_napcat_connection_enabled(
        self,
        settings: LiveAdapterSettings,
        *,
        enabled: bool,
        reason: str,
    ) -> bool:
        api_name = settings.napcat.control_api_name
        api = _ctx_attr(self, "api")
        if api is None or not hasattr(api, "call"):
            self._logger().warning("NapCat connection control skipped: api.call capability is unavailable.")
            return False
        try:
            result = await api.call(api_name, enabled=enabled, reason=reason)
        except Exception as exc:
            self._logger().warning(f"NapCat connection control failed: {exc}")
            return False
        if isinstance(result, Mapping) and result.get("success") is False:
            self._logger().warning(f"NapCat connection control was rejected: {result.get('error') or result}")
            return False
        return True

    async def _stop_runtime(self) -> None:
        if self._song_request_service is not None:
            await self._song_request_service.stop()
        self._song_request_service = None
        if self._song_request_console_session is not None:
            self._song_request_console_session.stop()
        self._song_request_console_session = None
        if self._local_voice_controller is not None:
            await self._local_voice_controller.stop()
        self._local_voice_controller = None
        if self._transport is not None:
            await self._transport.stop()
        self._transport = None
        if self._router is not None:
            self._router.reset()
        self._router = None
        self._planner = None
        if self._live2d_controller is not None:
            await self._live2d_controller.stop()
        self._live2d_controller = None
        if self._game_bridge is not None:
            await self._game_bridge.stop()
        self._game_bridge = None
        if self._sts2_controller is not None:
            await self._sts2_controller.stop()
        self._sts2_controller = None
        if self._sts2_log_session is not None:
            self._sts2_log_session.stop()
        self._sts2_log_session = None
        if self._tts_provider is not None:
            await self._tts_provider.stop()
        self._tts_provider = None
        self._subtitle_translator = None
        self._audio_output_player = None
        if self._subtitle_webui is not None:
            await self._subtitle_webui.stop()
        self._subtitle_webui = None
        if self._runtime_state is not None:
            await self._runtime_state.report_disconnected()

    async def _build_live2d_controller(self, settings: LiveAdapterSettings) -> Live2DController:
        websocket_url = settings.live2d.websocket_url
        if not websocket_url and settings.live2d.driver in {"auto", "vts"}:
            websocket_url = DEFAULT_VTS_WS_URL
        bridge = JsonLive2DBridge(
            http_url=settings.live2d.http_url,
            websocket_url=websocket_url,
            auth_token=settings.live2d.auth_token,
            connect_timeout_sec=settings.live2d.connect_timeout_sec,
            logger=self._logger(),
        )
        await bridge.start()
        probe = CapabilityProbe(bridge, logger=self._logger())
        profile = await probe.discover(
            driver=settings.live2d.driver,
            model_path=settings.live2d.adaptive.model_path,
            min_confidence=settings.live2d.adaptive.min_confidence,
            overrides=settings.live2d.overrides,
        )
        controller = Live2DController(
            bridge=bridge,
            profile=profile,
            chars_per_second=settings.live2d.sync.chars_per_second,
            prepare_ms=settings.live2d.sync.prepare_ms,
            release_ms=settings.live2d.sync.release_ms,
            mouth_update_interval_ms=settings.live2d.sync.mouth_update_interval_ms,
            mouth_closed_value=settings.live2d.sync.mouth_closed_value,
            mouth_open_threshold=settings.live2d.sync.mouth_open_threshold,
            mouth_open_gamma=settings.live2d.sync.mouth_open_gamma,
            mouth_open_gain=settings.live2d.sync.mouth_open_gain,
            mouth_open_max=settings.live2d.sync.mouth_open_max,
            mouth_sync_mode=settings.live2d.sync.mouth_sync_mode,
            mouth_amplitude_mix=settings.live2d.sync.mouth_amplitude_mix,
            mouth_viseme_lead_ms=settings.live2d.sync.mouth_viseme_lead_ms,
            mouth_open_smoothing=settings.live2d.sync.mouth_open_smoothing,
            mouth_open_attack_smoothing=settings.live2d.sync.mouth_open_attack_smoothing,
            mouth_open_release_smoothing=settings.live2d.sync.mouth_open_release_smoothing,
            mouth_open_min_delta=settings.live2d.sync.mouth_open_min_delta,
            mouth_form_smoothing=settings.live2d.sync.mouth_form_smoothing,
            mouth_form_min_delta=settings.live2d.sync.mouth_form_min_delta,
            mouth_keyframe_transition_ms=settings.live2d.sync.mouth_keyframe_transition_ms,
            mouth_vowel_shapes={
                "a": {
                    "open": settings.live2d.sync.mouth_vowel_a_open,
                    "form": settings.live2d.sync.mouth_vowel_a_form,
                },
                "e": {
                    "open": settings.live2d.sync.mouth_vowel_e_open,
                    "form": settings.live2d.sync.mouth_vowel_e_form,
                },
                "i": {
                    "open": settings.live2d.sync.mouth_vowel_i_open,
                    "form": settings.live2d.sync.mouth_vowel_i_form,
                },
                "o": {
                    "open": settings.live2d.sync.mouth_vowel_o_open,
                    "form": settings.live2d.sync.mouth_vowel_o_form,
                },
                "u": {
                    "open": settings.live2d.sync.mouth_vowel_u_open,
                    "form": settings.live2d.sync.mouth_vowel_u_form,
                },
            },
            parameter_keepalive_ms=settings.live2d.sync.parameter_keepalive_ms,
            lip_sync_only_mode=settings.live2d.sync.lip_sync_only_mode,
            idle_motion_enabled=settings.live2d.sync.idle_motion_enabled,
            idle_motion_model=settings.live2d.sync.idle_motion_model,
            idle_motion_name=settings.live2d.sync.idle_motion_name,
            idle_motion_file=settings.live2d.sync.idle_motion_file,
            idle_motion_interval_ms=settings.live2d.sync.idle_motion_interval_ms,
            idle_sway_enabled=settings.live2d.sync.idle_sway_enabled,
            idle_sway_interval_ms=settings.live2d.sync.idle_sway_interval_ms,
            idle_sway_intensity=settings.live2d.sync.idle_sway_intensity,
            speech_sway_enabled=settings.live2d.sync.speech_sway_enabled,
            speech_sway_intensity=settings.live2d.sync.speech_sway_intensity,
            speech_sway_update_interval_ms=settings.live2d.sync.speech_sway_update_interval_ms,
            logger=self._logger(),
        )
        await controller.start()
        return controller

    async def _build_tts_provider(self, settings: LiveAdapterSettings) -> TTSProviderProtocol:
        output_dir = str(_resolve_optional_path(settings.tts.output_dir) or (_plugin_data_dir() / "tts_output"))
        provider = GPTSoVITSTTSProvider(
            base_url=settings.tts.base_url,
            connect_timeout_sec=settings.tts.connect_timeout_sec,
            output_dir=output_dir,
            amplitude_interval_ms=settings.tts.amplitude_interval_ms,
            amplitude_normalization_enabled=settings.tts.amplitude_normalization_enabled,
            amplitude_noise_floor=settings.tts.amplitude_noise_floor,
            amplitude_peak_percentile=settings.tts.amplitude_peak_percentile,
            amplitude_normalization_gain=settings.tts.amplitude_normalization_gain,
            request_defaults={
                "text_lang": _tts_text_lang(settings),
                "ref_audio_path": str(_resolve_optional_path(settings.tts.ref_audio_path) or settings.tts.ref_audio_path),
                "aux_ref_audio_paths": [
                    str(resolved_path)
                    for raw_path in settings.tts.aux_ref_audio_paths
                    if (resolved_path := _resolve_optional_path(raw_path)) is not None
                ],
                "prompt_text": settings.tts.prompt_text,
                "prompt_lang": settings.tts.prompt_lang,
                "text_split_method": settings.tts.text_split_method,
                "top_k": settings.tts.top_k,
                "top_p": settings.tts.top_p,
                "temperature": settings.tts.temperature,
                "batch_size": settings.tts.batch_size,
                "batch_threshold": settings.tts.batch_threshold,
                "split_bucket": settings.tts.split_bucket,
                "speed_factor": settings.tts.speed_factor,
                "seed": settings.tts.seed,
                "parallel_infer": settings.tts.parallel_infer,
                "repetition_penalty": settings.tts.repetition_penalty,
            },
            logger=self._logger(),
        )
        await provider.start()
        return provider

    def _build_song_request_service(self, settings: LiveAdapterSettings) -> RvcSongRequestService:
        if not settings.song_request.is_available():
            raise RuntimeError("Song request and RVC functionality is disabled by configuration.")
        song_logger: Any = self._logger()
        console_session = self._song_request_console_session
        if settings.song_request.console_enabled:
            if console_session is None:
                console_session = SongRequestConsoleSession(
                    settings.song_request,
                    base_dir=_project_root(),
                    parent_logger=self._logger(),
                ).start()
                self._song_request_console_session = console_session
                if console_session.log_path is not None:
                    self._logger().info(f"Song request console logs are written to {console_session.log_path}")
            song_logger = console_session
        netease_client = NeteaseCloudMusicClient(
            base_url=settings.song_request.netease_api_base_url,
            app_id=settings.song_request.netease_app_id,
            app_secret=settings.song_request.netease_app_secret,
            public_key=settings.song_request.netease_public_key,
            private_key=settings.song_request.netease_private_key,
            access_token=settings.song_request.netease_access_token,
            token_cache_path=settings.song_request.netease_token_cache_path,
            device={
                "deviceId": settings.song_request.netease_device_id,
                "deviceType": settings.song_request.netease_device_type,
                "os": settings.song_request.netease_os,
                "appVer": settings.song_request.netease_app_ver,
                "channel": settings.song_request.netease_channel,
                "brand": settings.song_request.netease_brand,
                "model": settings.song_request.netease_model,
                "osVer": settings.song_request.netease_os_ver,
                "clientIp": settings.song_request.netease_client_ip,
                "flowFlag": settings.song_request.netease_flow_flag,
            },
            connect_timeout_sec=settings.song_request.connect_timeout_sec,
            request_timeout_sec=settings.song_request.request_timeout_sec,
            search_limit=settings.song_request.netease_search_limit,
            song_level=settings.song_request.netease_song_level,
            cookie=settings.song_request.netease_cookie,
            user_agent=settings.song_request.netease_user_agent,
            referer=settings.song_request.netease_referer,
            auto_qr_login_on_unauthorized=settings.song_request.netease_auto_qr_login_on_unauthorized,
            qr_login_timeout_sec=settings.song_request.netease_qr_login_timeout_sec,
            qr_login_poll_interval_sec=settings.song_request.netease_qr_poll_interval_sec,
            logger=song_logger,
        )
        pipeline = RvcSongPipeline(settings.song_request, logger=song_logger)

        def build_song_speech(path: Path, caption_text: str) -> SynthesizedSpeech:
            current_settings = self._load_settings()
            return build_synthesized_speech_from_wav(
                path,
                caption_text,
                provider="rvc_song",
                amplitude_interval_ms=current_settings.tts.amplitude_interval_ms,
                amplitude_normalization_enabled=current_settings.tts.amplitude_normalization_enabled,
                amplitude_noise_floor=current_settings.tts.amplitude_noise_floor,
                amplitude_peak_percentile=current_settings.tts.amplitude_peak_percentile,
                amplitude_normalization_gain=current_settings.tts.amplitude_normalization_gain,
            )

        async def render_ready(text: str, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
            current_settings = self._load_settings()
            return await self._render_local_reply(
                text,
                settings=current_settings,
                source_platform=PLATFORM_NAME,
                metadata=metadata,
                kwargs={},
                on_audio_start=self._build_sts2_audio_start_callback(PLATFORM_NAME, metadata),
            )

        async def render_song(
            caption_text: str,
            synthesized_speech: SynthesizedSpeech,
            metadata: Mapping[str, Any] | None = None,
        ) -> dict[str, Any]:
            current_settings = self._load_settings()
            return await self._render_external_audio_reply(
                caption_text,
                synthesized_speech,
                settings=current_settings,
                source_platform=PLATFORM_NAME,
                metadata=metadata,
                on_audio_start=self._build_sts2_audio_start_callback(PLATFORM_NAME, metadata),
            )

        return RvcSongRequestService(
            settings=settings.song_request,
            netease_client=netease_client,
            pipeline=pipeline,
            build_speech=build_song_speech,
            render_ready_reply=render_ready,
            render_song_reply=render_song,
            logger=song_logger,
            console_session=console_session,
        )

    async def _prepare_reply_delivery(
        self,
        text: str,
        *,
        settings: LiveAdapterSettings,
        metadata: Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
    ) -> tuple[str, str, dict[str, Any] | None, SynthesizedSpeech | None]:
        speech_text = str(text or "").strip()
        if not settings.language.is_english_voice_chinese_subtitle():
            audio_timeline, synthesized_speech = await self._resolve_reply_audio_timeline(
                speech_text,
                settings=settings,
                metadata=metadata,
                kwargs=kwargs,
            )
            return speech_text, speech_text, audio_timeline, synthesized_speech

        audio_task = asyncio.create_task(
            self._resolve_reply_audio_timeline(
                speech_text,
                settings=settings,
                metadata=metadata,
                kwargs=kwargs,
            ),
            name="maibot_bilibili_live_adapter.reply_tts",
        )
        subtitle_task = asyncio.create_task(
            self._prepare_subtitle_text(speech_text, settings=settings),
            name="maibot_bilibili_live_adapter.subtitle_translation",
        )
        (audio_timeline, synthesized_speech), subtitle_text = await asyncio.gather(audio_task, subtitle_task)
        return speech_text, subtitle_text, audio_timeline, synthesized_speech

    async def _prepare_subtitle_text(self, text: str, *, settings: LiveAdapterSettings) -> str:
        speech_text = str(text or "").strip()
        if not settings.language.is_english_voice_chinese_subtitle():
            return speech_text
        translator = self._subtitle_translator
        if translator is None or not speech_text:
            return speech_text
        try:
            translated = await translator.translate_to_chinese(speech_text)
        except Exception as exc:
            self._logger().warning(f"Subtitle translation failed; falling back to spoken text: {exc}")
            return speech_text
        return str(translated or "").strip() or speech_text

    async def _resolve_reply_audio_timeline(
        self,
        text: str,
        *,
        settings: LiveAdapterSettings,
        metadata: Mapping[str, Any] | None,
        kwargs: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, SynthesizedSpeech | None]:
        audio_timeline = _extract_audio_timeline(metadata, kwargs)
        if audio_timeline is not None:
            timeline_payload = dict(audio_timeline)
            if _uses_vts_native_lip_sync(settings):
                timeline_payload.pop("visemes", None)
            else:
                timeline_payload = _with_text_visemes(timeline_payload, text, settings=settings)
            return timeline_payload, None
        sts2_segments = _split_sts2_tts_segments(text, metadata, logger=self._logger())
        if len(sts2_segments) > 1:
            segment_tasks = [
                self._synthesize_reply_speech(segment_text, settings=settings)
                for segment_text in sts2_segments
            ]
            segment_results = await asyncio.gather(*segment_tasks)
            if all(result is not None for result in segment_results):
                segment_speeches = [cast(SynthesizedSpeech, result) for result in segment_results]
                merged_speech = _merge_synthesized_speeches(
                    segment_speeches,
                    text=text,
                    settings=settings,
                )
                if merged_speech is not None:
                    timeline_payload = merged_speech.to_audio_timeline()
                    timeline_payload["reply_segments"] = [
                        _build_reply_segment_payload(segment_text, speech)
                        for segment_text, speech in zip(sts2_segments, segment_speeches, strict=False)
                    ]
                    if _uses_vts_native_lip_sync(settings):
                        timeline_payload.pop("visemes", None)
                    else:
                        timeline_payload = _with_text_visemes(timeline_payload, text, settings=settings)
                    self._logger().info(
                        "Synthesized segmented STS2 GPT-SoVITS reply audio: "
                        f"segments={len(segment_speeches)} duration_ms={merged_speech.audio_duration_ms} "
                        f"audio_ref={merged_speech.audio_ref}"
                    )
                    return timeline_payload, merged_speech
            self._logger().warning(
                "Segmented STS2 GPT-SoVITS synthesis failed; falling back to single-pass synthesis."
            )
        synthesized_speech = await self._synthesize_reply_speech(text, settings=settings)
        if synthesized_speech is None:
            return None, None
        timeline_payload = synthesized_speech.to_audio_timeline()
        if _uses_vts_native_lip_sync(settings):
            timeline_payload.pop("visemes", None)
        else:
            timeline_payload = _with_text_visemes(timeline_payload, text, settings=settings)
        return timeline_payload, synthesized_speech

    async def _synthesize_reply_speech(
        self,
        text: str,
        *,
        settings: LiveAdapterSettings,
    ) -> SynthesizedSpeech | None:
        provider = self._tts_provider
        if provider is None or not settings.tts.enabled or not settings.tts.is_usable():
            return None
        try:
            result = await provider.synthesize(text)
        except Exception as exc:
            self._logger().warning(f"GPT-SoVITS synthesis failed: {exc}")
            return None
        self._logger().info(
            "Synthesized GPT-SoVITS reply audio: "
            f"duration_ms={result.audio_duration_ms} audio_ref={result.audio_ref}"
        )
        return result

    async def _start_subtitle_webui(self, settings: LiveAdapterSettings) -> None:
        webui = SubtitleWebUIService(
            host=settings.webui.host,
            port=settings.webui.port,
            subtitle_defaults={
                "box_width_px": settings.webui.subtitle.box_width_px,
                "box_height_px": settings.webui.subtitle.box_height_px,
                "left_px": settings.webui.subtitle.left_px,
                "bottom_px": settings.webui.subtitle.bottom_px,
                "background_color": settings.webui.subtitle.background_color,
                "font_family": settings.webui.subtitle.font_family,
                "font_size_px": settings.webui.subtitle.font_size_px,
                "text_color": settings.webui.subtitle.text_color,
            },
            logger=self._logger(),
        )
        try:
            await webui.start()
        except Exception as exc:
            self._logger().warning(f"Subtitle WebUI failed to start: {exc}")
            return
        self._subtitle_webui = webui

    async def _publish_reply_to_webui(
        self,
        text: str,
        *,
        settings: LiveAdapterSettings,
        source_platform: str,
        audio_timeline: Mapping[str, Any] | None,
        synthesized_speech: SynthesizedSpeech | None,
        speech_text: str = "",
        wait_for_audio_start: bool = False,
        on_audio_start: Callable[[], None] | None = None,
    ) -> tuple[bool, bool]:
        webui = self._subtitle_webui
        if webui is None or not settings.webui.enabled:
            return False, False
        reply_id = uuid4().hex
        segments = await self._build_webui_segments(
            text,
            settings=settings,
            speech_text=speech_text,
            audio_timeline=audio_timeline,
            synthesized_speech=synthesized_speech,
        )
        if not segments:
            return False, False
        await webui.publish_reply(
            reply_id=reply_id,
            text=text,
            segments=segments,
            source_platform=source_platform,
        )
        audio_started = False
        can_wait_for_audio = bool(wait_for_audio_start and getattr(webui, "has_clients", False))
        wait_for_audio = getattr(webui, "wait_for_audio_start", None)
        if can_wait_for_audio and callable(wait_for_audio):
            timeout_sec = max(0.1, settings.webui.audio_start_ack_timeout_ms / 1000.0)
            try:
                audio_start_event = await wait_for_audio(reply_id, timeout_sec=timeout_sec)
                audio_started = audio_start_event is not None
                if audio_started:
                    if on_audio_start is not None:
                        try:
                            on_audio_start()
                        except Exception as exc:
                            self._logger().warning(f"STS2 audio start callback failed: {exc}")
                    self._logger().debug(
                        "Subtitle WebUI audio start ACK received: "
                        f"reply_id={reply_id} segment={audio_start_event.get('segment_index', 0)}"
                    )
                else:
                    self._logger().warning(
                        "Subtitle WebUI audio start ACK timed out; "
                        "Live2D will use the normal prepare offset."
                    )
            except Exception as exc:
                self._logger().debug(f"Subtitle WebUI audio start wait failed: {exc}")
        return True, audio_started

    async def _build_webui_segments(
        self,
        text: str,
        *,
        settings: LiveAdapterSettings,
        audio_timeline: Mapping[str, Any] | None,
        synthesized_speech: SynthesizedSpeech | None,
        speech_text: str = "",
    ) -> list[SubtitleSegment]:
        normalized_speech_text = str(speech_text or "").strip()
        pre_synthesized_segments = _extract_reply_segment_payloads(audio_timeline)
        if pre_synthesized_segments:
            webui = self._subtitle_webui
            expose_webui_audio = _should_expose_webui_audio(settings)
            segments: list[SubtitleSegment] = []
            for index, payload in enumerate(pre_synthesized_segments):
                segment_text = str(payload.get("text") or "").strip()
                if not segment_text:
                    continue
                speech = _segment_audio_from_timeline(payload)
                duration_ms = (
                    speech.audio_duration_ms
                    if speech is not None
                    else estimate_subtitle_duration_ms(
                        segment_text,
                        chars_per_second=settings.live2d.sync.chars_per_second,
                    )
                )
                audio_ref = speech.audio_ref if speech is not None else ""
                audio_url = ""
                provider = speech.provider if speech is not None else ""
                segment_speech_text = str(
                    payload.get("speech_text") or payload.get("english_text") or payload.get("original_text") or ""
                ).strip()
                if not segment_speech_text and len(pre_synthesized_segments) == 1:
                    segment_speech_text = normalized_speech_text
                if expose_webui_audio and webui is not None and audio_ref:
                    try:
                        audio_url = webui.register_audio_asset(Path(audio_ref))
                    except Exception as exc:
                        self._logger().warning(f"Subtitle WebUI audio registration failed: {exc}")
                segments.append(
                    SubtitleSegment(
                        index=index,
                        text=segment_text,
                        duration_ms=max(120, int(duration_ms)),
                        audio_ref=audio_ref,
                        audio_url=audio_url,
                        provider=provider,
                        speech_text=segment_speech_text,
                    )
                )
            if segments:
                return segments
        raw_segments = split_text_segments(text)
        if not raw_segments:
            return []
        webui = self._subtitle_webui
        expose_webui_audio = _should_expose_webui_audio(settings)
        full_reply_speech = synthesized_speech or _segment_audio_from_timeline(audio_timeline)
        if full_reply_speech is not None:
            audio_url = ""
            if expose_webui_audio and webui is not None and full_reply_speech.audio_ref:
                try:
                    audio_url = webui.register_audio_asset(Path(full_reply_speech.audio_ref))
                except Exception as exc:
                    self._logger().warning(f"Subtitle WebUI audio registration failed: {exc}")
            return [
                SubtitleSegment(
                    index=0,
                    text=str(text or ""),
                    duration_ms=max(120, int(full_reply_speech.audio_duration_ms)),
                    audio_ref=full_reply_speech.audio_ref,
                    audio_url=audio_url,
                    provider=str(getattr(full_reply_speech, "provider", "") or ""),
                    speech_text=normalized_speech_text,
                )
            ]
        segments: list[SubtitleSegment] = []
        single_segment_audio = _segment_audio_from_timeline(audio_timeline) if len(raw_segments) == 1 else None
        for index, segment_text in enumerate(raw_segments):
            speech = synthesized_speech if len(raw_segments) == 1 else None
            if speech is None and index == 0 and single_segment_audio is not None:
                speech = single_segment_audio
            if speech is None and self._tts_provider is not None and settings.tts.enabled and settings.tts.is_usable():
                speech = await self._synthesize_reply_speech(segment_text, settings=settings)
            duration_ms = (
                speech.audio_duration_ms
                if speech is not None
                else estimate_subtitle_duration_ms(segment_text, chars_per_second=settings.live2d.sync.chars_per_second)
            )
            audio_ref = speech.audio_ref if speech is not None else ""
            audio_url = ""
            provider = speech.provider if speech is not None else ""
            if expose_webui_audio and webui is not None and audio_ref:
                try:
                    audio_url = webui.register_audio_asset(Path(audio_ref))
                except Exception as exc:
                    self._logger().warning(f"Subtitle WebUI audio registration failed: {exc}")
            segments.append(
                SubtitleSegment(
                    index=index,
                    text=segment_text,
                    duration_ms=max(120, int(duration_ms)),
                    audio_ref=audio_ref,
                    audio_url=audio_url,
                    provider=provider,
                    speech_text=normalized_speech_text if len(raw_segments) == 1 else "",
                )
            )
        return segments

    def _create_audio_playback_task(
        self,
        synthesized_speech: SynthesizedSpeech | None,
        *,
        settings: LiveAdapterSettings,
        enabled: bool,
        on_audio_start: Callable[[], None] | None = None,
    ) -> asyncio.Task[bool] | None:
        player = self._audio_output_player
        if (
            not enabled
            or player is None
            or synthesized_speech is None
            or not settings.tts.enabled
            or not settings.tts.audio_playback_enabled
            or not synthesized_speech.audio_ref
        ):
            return None
        return asyncio.create_task(
            player.play(
                synthesized_speech.audio_ref,
                duration_ms=synthesized_speech.audio_duration_ms,
                on_audio_start=on_audio_start,
            ),
            name="maibot_bilibili_live_adapter.audio_output",
        )

    def _build_sts2_audio_start_callback(
        self,
        source_platform: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> Callable[[], None] | None:
        controller = self._sts2_controller
        if (
            source_platform != PLATFORM_NAME
            or controller is None
            or not controller.has_pending_decision
        ):
            return None
        if not _metadata_matches_pending_sts2_decision(metadata, controller):
            return None
        return controller.build_audio_start_callback()

    def _load_settings(self) -> LiveAdapterSettings:
        try:
            return cast(LiveAdapterSettings, self.config)
        except RuntimeError:
            return LiveAdapterSettings.model_validate(self.get_default_config())

    def _handle_local_voice_settings_changed(self, settings: LiveAdapterSettings) -> None:
        self.set_plugin_config(settings.model_dump(mode="python"))

    async def _route_local_voice_text(self, text: str, metadata: Mapping[str, Any] | None = None) -> bool:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            return False
        settings = self._load_settings()
        normalized_metadata = dict(metadata or {})
        message_id = str(normalized_metadata.get("phrase_id") or f"local-voice-{uuid4().hex}").strip()
        message = build_local_voice_message_dict(
            settings,
            text=normalized_text,
            event_id=message_id,
            metadata=normalized_metadata,
        )
        route_metadata = {
            "source": "local_voice",
            "room_id": settings.bilibili.room_id,
            "selection_reason": "local_voice_priority",
            "selection_score": 1_000_000.0,
            "local_voice_priority": True,
        }
        accepted = await self.ctx.gateway.route_message(
            GATEWAY_NAME,
            message,
            route_metadata=route_metadata,
            external_message_id=message_id,
            dedupe_key=message_id,
        )
        if accepted:
            logger = self._logger()
            if logger is not None:
                logger.info(
                    "Local microphone transcript injected: "
                    f"phrase_id={message_id} text={normalized_text[:80]!r}"
                )
        return bool(accepted)

    def _logger(self) -> Any:
        try:
            return self.ctx.logger
        except RuntimeError:
            import logging

            return logging.getLogger("maibot_bilibili_live_adapter")


def create_plugin() -> BilibiliLiveAdapterPlugin:
    """Create the plugin instance."""

    return BilibiliLiveAdapterPlugin()


def _ctx_attr(plugin: BilibiliLiveAdapterPlugin, name: str) -> Any:
    try:
        return getattr(plugin.ctx, name)
    except RuntimeError:
        return None


def _build_live_chat_id(settings: LiveAdapterSettings) -> str:
    components = [PLATFORM_NAME]
    account_id = str(settings.identity.bot_user_id or "").strip()
    scope = str(settings.route_scope() or "").strip()
    room_id = str(settings.bilibili.room_id or "").strip()
    if account_id:
        components.append(f"account:{account_id}")
    if scope:
        components.append(f"scope:{scope}")
    components.append(room_id)
    return hashlib.md5("_".join(components).encode("utf-8")).hexdigest()


def _append_language_prompt(messages: list[dict[str, Any]], language_prompt: str) -> list[dict[str, Any]]:
    prompt = str(language_prompt or "").strip()
    if not prompt:
        return messages
    if not messages:
        return [{"role": "system", "content": prompt}]
    first_message = dict(messages[0])
    if str(first_message.get("role") or "").lower() != "system":
        return [{"role": "system", "content": prompt}, *messages]

    content = first_message.get("content")
    if isinstance(content, list):
        first_message["content"] = [*content, {"type": "text", "text": f"\n\n{prompt}"}]
    elif content is None:
        first_message["content"] = prompt
    else:
        first_message["content"] = f"{content}\n\n{prompt}"
    return [first_message, *messages[1:]]


def _remove_finish_tool_definitions(tool_definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered_tools: list[dict[str, Any]] = []
    for tool_definition in tool_definitions:
        if not isinstance(tool_definition, Mapping):
            filtered_tools.append(tool_definition)
            continue
        function_definition = tool_definition.get("function")
        if isinstance(function_definition, Mapping) and str(function_definition.get("name") or "").strip() == "finish":
            continue
        filtered_tools.append(dict(tool_definition))
    return filtered_tools


def _tts_text_lang(settings: LiveAdapterSettings) -> str:
    if settings.language.is_english_voice_chinese_subtitle():
        return "en"
    return str(settings.tts.text_lang or "").strip() or "zh"


def _webui_original_text_for_subtitle(
    speech_text: str,
    subtitle_text: str,
    settings: LiveAdapterSettings,
) -> str:
    """Return source text only when the subtitle is a distinct translated line."""

    normalized_speech_text = str(speech_text or "").strip()
    normalized_subtitle_text = str(subtitle_text or "").strip()
    if not settings.language.is_english_voice_chinese_subtitle():
        return ""
    if not normalized_speech_text or normalized_speech_text == normalized_subtitle_text:
        return ""
    return normalized_speech_text


def _plugin_data_dir() -> Path:
    return Path(__file__).resolve().parent / "data"


def _project_root() -> Path:
    start = Path(__file__).resolve()
    for parent in start.parents:
        if (parent / "pyproject.toml").exists() and (parent / "config").exists():
            return parent
    return Path.cwd().resolve()


def _resolve_optional_path(raw_path: str) -> Path | None:
    normalized_path = str(raw_path or "").strip()
    if not normalized_path:
        return None
    return Path(normalized_path).expanduser().resolve()


def _extract_audio_timeline(metadata: Mapping[str, Any] | None, kwargs: Mapping[str, Any]) -> dict[str, Any] | None:
    candidates = []
    if isinstance(metadata, Mapping):
        candidates.append(metadata.get("audio_timeline"))
    candidates.append(kwargs.get("audio_timeline"))
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            return dict(candidate)
    return None


def _with_text_visemes(
    audio_timeline: dict[str, Any],
    text: str,
    *,
    settings: LiveAdapterSettings,
) -> dict[str, Any]:
    if not settings.live2d.sync.viseme_timeline_enabled or isinstance(audio_timeline.get("visemes"), list):
        return audio_timeline
    duration_ms = _optional_int(audio_timeline.get("audio_duration_ms")) or 0
    if duration_ms <= 0 or not str(text or "").strip():
        return audio_timeline
    visemes = build_text_viseme_timeline(
        text,
        duration_ms,
        frame_interval_ms=settings.live2d.sync.mouth_update_interval_ms,
        mouth_vowel_shapes=_mouth_vowel_shapes_from_settings(settings),
        mouth_keyframe_transition_ms=settings.live2d.sync.mouth_keyframe_transition_ms,
        mouth_viseme_lead_ms=settings.live2d.sync.mouth_viseme_lead_ms,
    )
    if visemes:
        audio_timeline["visemes"] = visemes
    return audio_timeline


def _mouth_vowel_shapes_from_settings(settings: LiveAdapterSettings) -> dict[str, dict[str, float]]:
    return {
        "a": {"open": settings.live2d.sync.mouth_vowel_a_open, "form": settings.live2d.sync.mouth_vowel_a_form},
        "e": {"open": settings.live2d.sync.mouth_vowel_e_open, "form": settings.live2d.sync.mouth_vowel_e_form},
        "i": {"open": settings.live2d.sync.mouth_vowel_i_open, "form": settings.live2d.sync.mouth_vowel_i_form},
        "o": {"open": settings.live2d.sync.mouth_vowel_o_open, "form": settings.live2d.sync.mouth_vowel_o_form},
        "u": {"open": settings.live2d.sync.mouth_vowel_u_open, "form": settings.live2d.sync.mouth_vowel_u_form},
    }


def _extract_timeline_id(metadata: Mapping[str, Any] | None) -> str:
    if isinstance(metadata, Mapping):
        timeline_id = str(metadata.get("timeline_id") or "").strip()
        if timeline_id:
            return timeline_id
    return uuid4().hex


def _extract_platform(message: Mapping[str, Any]) -> str:
    platform = str(message.get("platform") or "").strip()
    if platform:
        return platform
    message_info = message.get("message_info")
    if isinstance(message_info, Mapping):
        additional_config = message_info.get("additional_config")
        if isinstance(additional_config, Mapping):
            for key in ("platform", "target_platform", "route_platform"):
                value = str(additional_config.get(key) or "").strip()
                if value:
                    return value
    return ""


def _extract_additional_config(metadata: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(metadata, Mapping):
        return {}
    message_info = metadata.get("message_info")
    if not isinstance(message_info, Mapping):
        return {}
    additional_config = message_info.get("additional_config")
    if not isinstance(additional_config, Mapping):
        return {}
    return additional_config


def _build_render_metadata(
    message: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if not isinstance(message, Mapping):
        return metadata
    if not isinstance(metadata, Mapping) or not metadata:
        return message
    merged = dict(metadata)
    merged.update(dict(message))
    return merged


def _metadata_matches_pending_sts2_decision(
    metadata: Mapping[str, Any] | None,
    controller: Any,
) -> bool:
    additional_config = _extract_additional_config(metadata)
    event_type = str(additional_config.get("live_event_type") or "").strip().lower()
    if event_type != "sts2_decision":
        return False
    metadata_decision_id = str(additional_config.get("sts2_decision_id") or "").strip()
    pending_decision_id = str(getattr(controller, "pending_decision_id", "") or "").strip()
    return bool(metadata_decision_id and pending_decision_id and metadata_decision_id == pending_decision_id)


def _split_sts2_tts_segments(
    text: str,
    metadata: Mapping[str, Any] | None,
    *,
    logger: Any = None,
) -> list[str]:
    normalized_text = str(text or "").strip()
    if not normalized_text:
        return []
    additional_config = _extract_additional_config(metadata)
    live_event_type = str(additional_config.get("live_event_type") or "").strip().lower()
    if not (bool(additional_config.get("sts2_priority")) or live_event_type.startswith("sts2")):
        return [normalized_text]
    try:
        from src.maisaka.builtin_tool.context import BuiltinToolRuntimeContext

        segments = BuiltinToolRuntimeContext.post_process_reply_text(normalized_text)
    except Exception as exc:
        if logger is not None:
            logger.warning(f"Failed to use MaiBot reply post-process for STS2 TTS splitting: {exc}")
        return [normalized_text]
    normalized_segments = [str(segment or "").strip() for segment in segments if str(segment or "").strip()]
    return normalized_segments or [normalized_text]


def _tts_output_dir(settings: LiveAdapterSettings) -> Path:
    return Path(_resolve_optional_path(settings.tts.output_dir) or (_plugin_data_dir() / "tts_output")).expanduser()


def _merge_synthesized_speeches(
    speeches: list[SynthesizedSpeech],
    *,
    text: str,
    settings: LiveAdapterSettings,
) -> SynthesizedSpeech | None:
    if not speeches:
        return None
    output_dir = _tts_output_dir(settings)
    output_dir.mkdir(parents=True, exist_ok=True)
    merged_path = output_dir / f"sts2-merged-{uuid4().hex}.wav"
    try:
        sample_signature: tuple[int, int, int, str, str] | None = None
        with wave.open(str(merged_path), "wb") as merged_wav:
            for speech in speeches:
                source_path = Path(speech.audio_ref).expanduser().resolve()
                with wave.open(str(source_path), "rb") as source_wav:
                    current_signature = (
                        source_wav.getnchannels(),
                        source_wav.getsampwidth(),
                        source_wav.getframerate(),
                        source_wav.getcomptype(),
                        source_wav.getcompname(),
                    )
                    if sample_signature is None:
                        sample_signature = current_signature
                        merged_wav.setnchannels(current_signature[0])
                        merged_wav.setsampwidth(current_signature[1])
                        merged_wav.setframerate(current_signature[2])
                        merged_wav.setcomptype(current_signature[3], current_signature[4])
                    elif current_signature != sample_signature:
                        raise ValueError("incompatible wav parameters across segmented STS2 TTS outputs")
                    merged_wav.writeframes(source_wav.readframes(source_wav.getnframes()))
    except Exception:
        try:
            merged_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return build_synthesized_speech_from_wav(
        merged_path,
        text,
        provider=speeches[0].provider,
        amplitude_interval_ms=settings.tts.amplitude_interval_ms,
        amplitude_normalization_enabled=settings.tts.amplitude_normalization_enabled,
        amplitude_noise_floor=settings.tts.amplitude_noise_floor,
        amplitude_peak_percentile=settings.tts.amplitude_peak_percentile,
        amplitude_normalization_gain=settings.tts.amplitude_normalization_gain,
    )


def _build_reply_segment_payload(text: str, speech: SynthesizedSpeech) -> dict[str, Any]:
    payload = speech.to_audio_timeline()
    payload["text"] = str(text or "").strip()
    return payload


def _extract_reply_segment_payloads(audio_timeline: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(audio_timeline, Mapping):
        return []
    raw_segments = audio_timeline.get("reply_segments")
    if not isinstance(raw_segments, list):
        return []
    normalized_segments: list[dict[str, Any]] = []
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, Mapping):
            continue
        segment_text = str(raw_segment.get("text") or "").strip()
        if not segment_text:
            continue
        normalized_segments.append(dict(raw_segment))
    return normalized_segments


def _build_local_render_only_modified_kwargs(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    modified_kwargs = dict(kwargs)
    modified_kwargs["set_reply"] = False
    modified_kwargs["reply_message_id"] = None
    return modified_kwargs


def _segment_audio_from_timeline(audio_timeline: Mapping[str, Any] | None) -> SynthesizedSpeech | None:
    if not isinstance(audio_timeline, Mapping):
        return None
    audio_ref = str(audio_timeline.get("audio_ref") or "").strip()
    if not audio_ref:
        return None
    duration_ms = _optional_int(audio_timeline.get("audio_duration_ms")) or 0
    amplitudes = audio_timeline.get("amplitudes")
    return SynthesizedSpeech(
        provider=str(audio_timeline.get("provider") or "").strip() or "external",
        text="",
        audio_ref=audio_ref,
        audio_duration_ms=max(0, duration_ms),
        sample_rate=0,
        amplitudes=list(amplitudes) if isinstance(amplitudes, list) else [],
        amplitude_stats=dict(audio_timeline.get("amplitude_stats") or {}),
        content_type=str(audio_timeline.get("content_type") or "audio/wav"),
    )


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _uses_vts_native_lip_sync(settings: LiveAdapterSettings) -> bool:
    return str(settings.live2d.sync.mouth_sync_mode or "").strip().lower() == "vts_native"


def _should_expose_webui_audio(settings: LiveAdapterSettings) -> bool:
    return not (_uses_vts_native_lip_sync(settings) and settings.tts.audio_playback_enabled)


def _infer_emotion_intent(text: str) -> str:
    lowered = text.lower()
    if any(token in text for token in ("哈哈", "开心", "太棒", "好耶")) or any(token in lowered for token in ("haha", "lol")):
        return "react_happy"
    if any(token in text for token in ("？！", "！", "?", "惊", "震惊")):
        return "react_surprised"
    if any(token in text for token in ("害羞", "脸红", "不好意思")):
        return "react_shy"
    if any(token in text for token in ("？", "?", "怎么", "为什么")):
        return "react_confused"
    return ""
