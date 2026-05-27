"""Local bridge from MaiBot Live2D JSON events to the VTube Studio public API.

Run this script while VTube Studio is open and "Allow Plugin API access" is enabled.
The MaiBot plugin connects to this bridge, and this bridge translates parameter frames
to VTS InjectParameterDataRequest calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import signal
import string
import time
import uuid
from urllib.parse import urlparse

from aiohttp import ClientConnectionError, ClientSession, ClientTimeout, WSMsgType, web


API_NAME = "VTubeStudioPublicAPI"
API_VERSION = "1.0"
_AUTH_MESSAGE_TYPES = {"APIStateRequest", "AuthenticationTokenRequest", "AuthenticationRequest"}
DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 18081
DEFAULT_LISTEN_PATH = "/live2d"
DEFAULT_VTS_URL = "ws://127.0.0.1:8002"
DEFAULT_PLUGIN_NAME = "MaiBot Live2D Bridge"
DEFAULT_PLUGIN_DEVELOPER = "MaiBot"

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOKEN_FILE = PLUGIN_ROOT / "data" / "vts_auth_token.json"

FORBIDDEN_ID_TOKENS = ("hand", "arm", "leg", "foot", "finger", "gesture")
MOTION_HOTKEY_TYPES = {"ChangeIdleAnimation", "TriggerAnimation"}
MOTION_IDLE_TYPE_BONUS = {"ChangeIdleAnimation": 20, "TriggerAnimation": 10}
TIMELINE_INTERPOLATION_INTERVAL_MS = 20

DEFAULT_PARAMETER_MAPPING: dict[str, list[str]] = {
    "ParamAngleX": ["FaceAngleX"],
    "ParamAngleY": ["FaceAngleY"],
    "ParamAngleZ": ["FaceAngleZ"],
    "ParamBodyAngleX": ["BodyAngleX"],
    "ParamBodyAngleY": ["BodyAngleY"],
    "ParamBodyAngleZ": ["BodyAngleZ"],
    "ParamEyeLOpen": ["EyeOpenLeft"],
    "ParamEyeROpen": ["EyeOpenRight"],
    "ParamEyeBallX": ["EyeLeftX", "EyeRightX"],
    "ParamEyeBallY": ["EyeLeftY", "EyeRightY"],
    "ParamMouthOpenY": ["MouthOpen"],
    "ParamMouthSmile": ["MouthSmile"],
    "ParamMouthForm": ["MouthX"],
    "ParamBrowLY": ["BrowLeftY"],
    "ParamBrowRY": ["BrowRightY"],
    "ParamCheek": ["CheekPuff"],
}

FALLBACK_LIVE2D_PARAMETERS: list[dict[str, float | str]] = [
    {"id": "ParamAngleX", "min": -30.0, "max": 30.0, "default": 0.0, "current": 0.0},
    {"id": "ParamAngleY", "min": -30.0, "max": 30.0, "default": 0.0, "current": 0.0},
    {"id": "ParamAngleZ", "min": -30.0, "max": 30.0, "default": 0.0, "current": 0.0},
    {"id": "ParamBodyAngleX", "min": -10.0, "max": 10.0, "default": 0.0, "current": 0.0},
    {"id": "ParamBodyAngleY", "min": -10.0, "max": 10.0, "default": 0.0, "current": 0.0},
    {"id": "ParamBodyAngleZ", "min": -10.0, "max": 10.0, "default": 0.0, "current": 0.0},
    {"id": "ParamEyeLOpen", "min": 0.0, "max": 1.0, "default": 1.0, "current": 1.0},
    {"id": "ParamEyeROpen", "min": 0.0, "max": 1.0, "default": 1.0, "current": 1.0},
    {"id": "ParamEyeLSmile", "min": 0.0, "max": 1.0, "default": 0.0, "current": 0.0},
    {"id": "ParamEyeRSmile", "min": 0.0, "max": 1.0, "default": 0.0, "current": 0.0},
    {"id": "ParamEyeBallX", "min": -1.0, "max": 1.0, "default": 0.0, "current": 0.0},
    {"id": "ParamEyeBallY", "min": -1.0, "max": 1.0, "default": 0.0, "current": 0.0},
    {"id": "ParamMouthOpenY", "min": 0.0, "max": 1.0, "default": 0.0, "current": 0.0},
    {"id": "ParamMouthForm", "min": -2.0, "max": 1.0, "default": 0.0, "current": 0.0},
    {"id": "ParamMouthSmile", "min": 0.0, "max": 1.0, "default": 0.0, "current": 0.0},
    {"id": "ParamBrowLY", "min": -1.0, "max": 1.0, "default": 0.0, "current": 0.0},
    {"id": "ParamBrowRY", "min": -1.0, "max": 1.0, "default": 0.0, "current": 0.0},
    {"id": "ParamCheek", "min": 0.0, "max": 1.0, "default": 0.0, "current": 0.0},
]


@dataclass
class BridgeConfig:
    listen_host: str = DEFAULT_LISTEN_HOST
    listen_port: int = DEFAULT_LISTEN_PORT
    listen_path: str = DEFAULT_LISTEN_PATH
    vts_url: str = DEFAULT_VTS_URL
    plugin_name: str = DEFAULT_PLUGIN_NAME
    plugin_developer: str = DEFAULT_PLUGIN_DEVELOPER
    token_file: Path = DEFAULT_TOKEN_FILE
    mapping_file: Path | None = None
    create_custom_parameters: bool = True
    dry_run: bool = False


@dataclass
class ParameterMapper:
    """Resolve MaiBot Live2D parameter IDs to VTS input parameter IDs."""

    mapping: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_PARAMETER_MAPPING))
    input_parameter_names: set[str] = field(default_factory=set)
    create_custom_parameters: bool = True
    custom_name_by_parameter_id: dict[str, str] = field(default_factory=dict)

    def resolve_existing_targets(self, parameter_id: str) -> list[str]:
        if is_forbidden_parameter_id(parameter_id):
            return []
        explicit_targets = self.mapping.get(parameter_id, [])
        existing_targets = [target for target in explicit_targets if target in self.input_parameter_names]
        if existing_targets:
            return existing_targets
        if parameter_id in self.input_parameter_names:
            return [parameter_id]
        custom_name = self.custom_name_by_parameter_id.get(parameter_id)
        if custom_name and custom_name in self.input_parameter_names:
            return [custom_name]
        return []

    def custom_name_for(self, parameter_id: str) -> str:
        existing = self.custom_name_by_parameter_id.get(parameter_id)
        if existing:
            return existing
        candidate = sanitize_vts_parameter_name(parameter_id)
        self.custom_name_by_parameter_id[parameter_id] = candidate
        return candidate

    def update_input_names(self, names: set[str]) -> None:
        self.input_parameter_names = set(names)


class VTubeStudioClient:
    """Small async VTube Studio API client."""

    def __init__(self, config: BridgeConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self._session: ClientSession | None = None
        self._ws: Any = None
        self._request_lock = asyncio.Lock()
        self._authenticated = False

    async def connect(self) -> None:
        if self.config.dry_run:
            return
        if self._session is None:
            timeout = ClientTimeout(total=None, connect=10.0, sock_read=10.0)
            self._session = ClientSession(timeout=timeout)
        if self._ws is None or self._ws.closed:
            self.logger.info("Connecting to VTube Studio API at %s", self.config.vts_url)
            self._ws = await self._session.ws_connect(self.config.vts_url)
            self._authenticated = False

    async def close(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        self._ws = None
        self._authenticated = False
        if self._session is not None:
            with contextlib.suppress(Exception):
                await self._session.close()
        self._session = None

    async def ensure_authenticated(self) -> None:
        if self.config.dry_run:
            self.logger.info("Dry-run mode enabled; skipping VTS authentication")
            return
        if self._authenticated and self._ws is not None and not self._ws.closed:
            return
        await self.connect()
        state = await self.request("APIStateRequest", authenticate=False)
        if _response_data(state).get("currentSessionAuthenticated") is True:
            self.logger.info("Current VTS session is already authenticated")
            self._authenticated = True
            return

        token = self._load_token()
        if token and await self._authenticate_with_token(token):
            return

        self.logger.info("Requesting VTS auth token; click Allow in VTube Studio")
        token_response = await self.request(
            "AuthenticationTokenRequest",
            {
                "pluginName": self.config.plugin_name,
                "pluginDeveloper": self.config.plugin_developer,
            },
            authenticate=False,
        )
        token = str(_response_data(token_response).get("authenticationToken") or "").strip()
        if not token:
            raise RuntimeError(f"VTS did not return an authentication token: {token_response}")
        self._save_token(token)
        if not await self._authenticate_with_token(token):
            raise RuntimeError("VTS authentication failed after receiving a token")

    async def request(
        self,
        message_type: str,
        data: Mapping[str, Any] | None = None,
        *,
        authenticate: bool = True,
        retries: int = 1,
    ) -> dict[str, Any]:
        if self.config.dry_run:
            return {"messageType": message_type.replace("Request", "Response"), "data": {}}
        if authenticate and message_type not in _AUTH_MESSAGE_TYPES:
            await self.ensure_authenticated()
        last_error: Exception | None = None
        for attempt in range(max(1, retries + 1)):
            try:
                return await self._request_once(message_type, data)
            except (ClientConnectionError, ConnectionResetError, RuntimeError) as exc:
                last_error = exc
                await self._reset_ws()
                if attempt >= retries:
                    break
                self.logger.warning("VTS request %s failed; reconnecting: %s", message_type, exc)
                if authenticate and message_type not in _AUTH_MESSAGE_TYPES:
                    await self.ensure_authenticated()
        raise last_error or RuntimeError(f"VTS request failed: {message_type}")

    async def _request_once(self, message_type: str, data: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        request = {
            "apiName": API_NAME,
            "apiVersion": API_VERSION,
            "requestID": request_id,
            "messageType": message_type,
            "data": dict(data or {}),
        }
        async with self._request_lock:
            await self.connect()
            await self._ws.send_str(json.dumps(request, ensure_ascii=False))
            while True:
                response = await self._ws.receive()
                if response.type != WSMsgType.TEXT:
                    if response.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                        raise ClientConnectionError(f"VTS websocket closed while waiting for {message_type}")
                    raise RuntimeError(f"Unexpected VTS websocket message type: {response.type}")
                payload = json.loads(response.data)
                if not isinstance(payload, dict):
                    continue
                if payload.get("requestID") not in {request_id, "", None}:
                    continue
                if payload.get("messageType") == "APIError":
                    raise RuntimeError(json.dumps(payload.get("data") or payload, ensure_ascii=False))
                return payload

    async def _authenticate_with_token(self, token: str) -> bool:
        try:
            response = await self.request(
                "AuthenticationRequest",
                {
                    "pluginName": self.config.plugin_name,
                    "pluginDeveloper": self.config.plugin_developer,
                    "authenticationToken": token,
                },
                authenticate=False,
            )
        except Exception as exc:
            self.logger.warning("VTS authentication token failed: %s", exc)
            return False
        authenticated = bool(_response_data(response).get("authenticated"))
        if authenticated:
            self.logger.info("Authenticated with VTube Studio")
            self._authenticated = True
        else:
            self.logger.warning("VTS authentication returned false: %s", response)
        return authenticated

    async def _reset_ws(self) -> None:
        ws = self._ws
        self._ws = None
        self._authenticated = False
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    def _load_token(self) -> str:
        try:
            raw = json.loads(self.config.token_file.read_text(encoding="utf-8"))
        except Exception:
            return ""
        return str(raw.get("authenticationToken") or "").strip() if isinstance(raw, Mapping) else ""

    def _save_token(self, token: str) -> None:
        self.config.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.config.token_file.write_text(
            json.dumps({"authenticationToken": token}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.logger.info("Saved VTS auth token to %s", self.config.token_file)


class MaiBotVTubeStudioBridge:
    """Bridge server that accepts MaiBot Live2D events and injects VTS inputs."""

    def __init__(self, config: BridgeConfig, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.vts = VTubeStudioClient(config, logger)
        self.mapper = ParameterMapper(
            mapping=merge_parameter_mapping(load_mapping_file(config.mapping_file)),
            create_custom_parameters=config.create_custom_parameters,
        )
        self.live2d_specs: dict[str, dict[str, Any]] = {}
        self.timeline_origins: dict[str, float] = {}
        self.timeline_keyframes: dict[str, Mapping[str, Any]] = {}
        self._inject_queue: asyncio.PriorityQueue[tuple[float, int, Mapping[str, Any]]] = asyncio.PriorityQueue()
        self._inject_worker: asyncio.Task[None] | None = None
        self._inject_sequence = 0
        self._started = False
        self._inject_success_count = 0
        self._last_inject_log_at = 0.0

    async def start(self) -> None:
        if self._started:
            return
        await self.vts.ensure_authenticated()
        await self.refresh_input_parameters()
        self._start_inject_worker()
        self._started = True

    async def close(self) -> None:
        if self._inject_worker is not None:
            self._inject_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._inject_worker
        self._inject_worker = None
        await self.vts.close()

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.logger.info("MaiBot Live2D client connected from %s", request.remote)
        try:
            await self.start()
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    await self.handle_plugin_payload(json.loads(message.data), ws)
                elif message.type == WSMsgType.ERROR:
                    self.logger.warning("MaiBot websocket error: %s", ws.exception())
        except Exception as exc:
            self.logger.exception("Bridge websocket handler failed: %s", exc)
        finally:
            self.logger.info("MaiBot Live2D client disconnected")
        return ws

    async def handle_plugin_payload(self, payload: Mapping[str, Any], ws: web.WebSocketResponse) -> None:
        event_type = str(payload.get("type") or "").strip()
        if event_type == "live2d.capabilities.request":
            await ws.send_str(json.dumps(await self.build_capabilities_response(), ensure_ascii=False))
            return
        if event_type == "bot_reply.prepare":
            timeline_id = str(payload.get("timeline_id") or "").strip()
            if timeline_id:
                self.timeline_origins[timeline_id] = time.monotonic()
                self.timeline_keyframes.pop(timeline_id, None)
            self.logger.info("Prepared timeline %s text=%r", timeline_id, str(payload.get("text") or "")[:40])
            return
        if event_type in {"bot_reply.start", "bot_reply.end"}:
            self.logger.info("Timeline event %s id=%s", event_type, payload.get("timeline_id"))
            return
        if event_type == "live2d.motion":
            result = await self.trigger_motion(payload)
            if not result.get("success", False):
                self.logger.warning("VTS motion trigger failed: %s", result)
            return
        if event_type in {"live2d.parameters", "live2d.timeline.frame"}:
            self._schedule_or_inject(payload)
            return
        self.logger.debug("Ignoring bridge event type=%s", event_type)

    async def build_capabilities_response(self) -> dict[str, Any]:
        try:
            response = await self.vts.request("Live2DParameterListRequest")
            data = _response_data(response)
            raw_parameters = data.get("parameters") if isinstance(data.get("parameters"), list) else []
            parameters = [convert_vts_live2d_parameter(item) for item in raw_parameters if isinstance(item, Mapping)]
            if not parameters:
                parameters = [dict(item) for item in FALLBACK_LIVE2D_PARAMETERS]
            self.live2d_specs = {str(item["id"]): dict(item) for item in parameters}
            return {
                "type": "live2d.capabilities.response",
                "model_id": str(data.get("modelID") or data.get("modelId") or ""),
                "model_name": str(data.get("modelName") or ""),
                "parameters": parameters,
                "groups": infer_groups(parameters),
            }
        except Exception as exc:
            self.logger.warning("Failed to request VTS Live2D parameters; using fallback profile: %s", exc)
            parameters = [dict(item) for item in FALLBACK_LIVE2D_PARAMETERS]
            self.live2d_specs = {str(item["id"]): dict(item) for item in parameters}
            return {
                "type": "live2d.capabilities.response",
                "model_id": "fallback",
                "model_name": "VTube Studio fallback",
                "parameters": parameters,
                "groups": infer_groups(parameters),
            }

    async def refresh_input_parameters(self) -> None:
        if self.config.dry_run:
            names = set().union(*DEFAULT_PARAMETER_MAPPING.values())
            self.mapper.update_input_names(names)
            return
        response = await self.vts.request("InputParameterListRequest")
        data = _response_data(response)
        names: set[str] = set()
        for key in ("defaultParameters", "customParameters"):
            raw_parameters = data.get(key)
            if not isinstance(raw_parameters, list):
                continue
            for raw_parameter in raw_parameters:
                if isinstance(raw_parameter, Mapping):
                    name = str(raw_parameter.get("name") or "").strip()
                    if name:
                        names.add(name)
        self.mapper.update_input_names(names)
        self.logger.info("VTS input parameters loaded: %d", len(names))

    async def trigger_motion(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        motion_name = str(payload.get("motion") or payload.get("name") or "m01").strip()
        motion_file = str(payload.get("motion_file") or payload.get("file") or "").strip()
        model_name = str(payload.get("model") or "").strip()
        if not motion_file and motion_name:
            motion_file = f"{motion_name}.motion3.json"
        if not motion_name and not motion_file:
            return {"success": False, "error": "motion name or file is required"}
        if self.config.dry_run:
            self.logger.info("DRY RUN trigger motion: model=%s motion=%s file=%s", model_name, motion_name, motion_file)
            return {"success": True, "dry_run": True}
        response = await self.vts.request("HotkeysInCurrentModelRequest")
        data = _response_data(response)
        hotkeys = data.get("availableHotkeys") if isinstance(data.get("availableHotkeys"), list) else []
        selected = select_motion_hotkey(hotkeys, motion_name=motion_name, motion_file=motion_file, model_name=model_name)
        if not selected:
            return {
                "success": False,
                "error": "no matching VTS motion hotkey",
                "motion": motion_name,
                "motion_file": motion_file,
                "available_hotkey_count": len(hotkeys),
            }
        hotkey_id = str(selected.get("hotkeyID") or selected.get("id") or selected.get("name") or "").strip()
        if not hotkey_id:
            return {"success": False, "error": "matching VTS motion hotkey has no id", "hotkey": dict(selected)}
        trigger_response = await self.vts.request("HotkeyTriggerRequest", {"hotkeyID": hotkey_id})
        self.logger.info(
            "Triggered VTS motion hotkey: model=%s motion=%s file=%s hotkey=%s",
            model_name or "current",
            motion_name,
            motion_file,
            hotkey_id,
        )
        return {"success": True, "hotkey_id": hotkey_id, "hotkey": dict(selected), "response": trigger_response}

    def _schedule_or_inject(self, payload: Mapping[str, Any]) -> None:
        if str(payload.get("type") or "").strip() == "live2d.timeline.frame":
            self._schedule_timeline_frame(payload)
            return
        self._schedule_payload(payload)

    def _schedule_timeline_frame(self, payload: Mapping[str, Any]) -> None:
        timeline_id = str(payload.get("timeline_id") or "").strip()
        if not timeline_id:
            self._schedule_payload(payload)
            return
        current = dict(payload)
        current["keyframe"] = True
        previous = self.timeline_keyframes.get(timeline_id)
        if previous is None:
            self.timeline_keyframes[timeline_id] = current
            self._schedule_payload(current)
            return
        frames = interpolate_timeline_keyframes(previous, current)
        if not frames:
            frames = [current]
        for frame in frames:
            self._schedule_payload(frame)
        self.timeline_keyframes[timeline_id] = current

    def _schedule_payload(self, payload: Mapping[str, Any]) -> None:
        self._start_inject_worker()
        due_at = time.monotonic() + self._compute_delay_seconds(payload)
        self._inject_sequence += 1
        self._inject_queue.put_nowait((due_at, self._inject_sequence, dict(payload)))

    def _start_inject_worker(self) -> None:
        if self._inject_worker is None or self._inject_worker.done():
            self._inject_worker = asyncio.create_task(self._inject_worker_loop(), name="vts_bridge.inject_worker")

    async def _inject_worker_loop(self) -> None:
        while True:
            due_at, _sequence, payload = await self._inject_queue.get()
            try:
                delay = due_at - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                result = await self.inject_event_parameters(payload)
                if not result.get("success", False):
                    self.logger.warning("VTS injection failed: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning("VTS injection task failed: %s", exc)
            finally:
                self._inject_queue.task_done()

    async def inject_event_parameters(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        raw_parameters = payload.get("parameters")
        if not isinstance(raw_parameters, list):
            return {"success": False, "error": "parameters must be a list"}
        parameter_values: list[dict[str, float | str]] = []
        for raw_parameter in raw_parameters:
            if not isinstance(raw_parameter, Mapping):
                continue
            parameter_values.extend(await self._to_vts_parameter_values(raw_parameter))
        if not parameter_values:
            return {"success": True, "skipped": True, "reason": "no mapped VTS inputs"}
        if self.config.dry_run:
            self.logger.info("DRY RUN inject: %s", parameter_values)
            return {"success": True, "dry_run": True}
        response = await self.vts.request(
            "InjectParameterDataRequest",
            {
                "faceFound": True,
                "mode": "set",
                "parameterValues": parameter_values,
            },
        )
        self._log_injection_success(parameter_values)
        self.logger.debug("Injected %d VTS parameter values", len(parameter_values))
        return {"success": True, "response": response}

    def _log_injection_success(self, parameter_values: list[Mapping[str, float | str]]) -> None:
        self._inject_success_count += 1
        now = time.monotonic()
        if self._inject_success_count <= 3 or now - self._last_inject_log_at >= 2.0:
            sample = ", ".join(str(item.get("id") or "") for item in parameter_values[:4])
            self.logger.info(
                "Injected VTS parameter batch #%d: %d values [%s]",
                self._inject_success_count,
                len(parameter_values),
                sample,
            )
            self._last_inject_log_at = now

    async def _to_vts_parameter_values(self, raw_parameter: Mapping[str, Any]) -> list[dict[str, float | str]]:
        parameter_id = str(raw_parameter.get("id") or "").strip()
        if not parameter_id or is_forbidden_parameter_id(parameter_id):
            return []
        value = _as_float(raw_parameter.get("value"))
        weight = min(1.0, max(0.0, _as_float(raw_parameter.get("weight"), default=1.0)))
        targets = self.mapper.resolve_existing_targets(parameter_id)
        if not targets and self.config.create_custom_parameters:
            custom_name = await self.ensure_custom_parameter(parameter_id)
            if custom_name:
                targets = [custom_name]
        return [{"id": target, "value": value, "weight": weight} for target in targets]

    async def ensure_custom_parameter(self, parameter_id: str) -> str:
        custom_name = self.mapper.custom_name_for(parameter_id)
        if custom_name in self.mapper.input_parameter_names:
            return custom_name
        spec = self.live2d_specs.get(parameter_id, default_spec_for(parameter_id))
        if self.config.dry_run:
            self.mapper.input_parameter_names.add(custom_name)
            return custom_name
        try:
            await self.vts.request(
                "ParameterCreationRequest",
                {
                    "parameterName": custom_name,
                    "explanation": f"MaiBot bridge input for {parameter_id}",
                    "min": _as_float(spec.get("min"), default=-1.0),
                    "max": _as_float(spec.get("max"), default=1.0),
                    "defaultValue": _as_float(spec.get("default"), default=0.0),
                },
            )
        except Exception as exc:
            self.logger.warning("Failed to create VTS custom parameter %s: %s", custom_name, exc)
            return ""
        self.mapper.input_parameter_names.add(custom_name)
        self.logger.info("Created VTS custom parameter %s for %s", custom_name, parameter_id)
        return custom_name

    def _compute_delay_seconds(self, payload: Mapping[str, Any]) -> float:
        timeline_id = str(payload.get("timeline_id") or "").strip()
        if not timeline_id:
            return 0.0
        origin = self.timeline_origins.setdefault(timeline_id, time.monotonic())
        offset_ms = max(0, int(_as_float(payload.get("offset_ms"), default=0.0)))
        elapsed = time.monotonic() - origin
        return max(0.0, offset_ms / 1000.0 - elapsed)


def build_app(bridge: MaiBotVTubeStudioBridge) -> web.Application:
    app = web.Application()
    app.router.add_get(bridge.config.listen_path, bridge.handle_ws)
    return app


async def run_bridge(config: BridgeConfig) -> None:
    logger = logging.getLogger("vtube_studio_bridge")
    bridge = MaiBotVTubeStudioBridge(config, logger)
    app = build_app(bridge)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.listen_host, config.listen_port)
    await site.start()
    client_host = "127.0.0.1" if config.listen_host in {"0.0.0.0", "::"} else config.listen_host
    logger.info(
        "MaiBot VTS bridge listening at ws://%s:%s%s; configure MaiBot to connect to ws://%s:%s%s",
        config.listen_host,
        config.listen_port,
        config.listen_path,
        client_host,
        config.listen_port,
        config.listen_path,
    )
    _warn_if_ports_overlap(config, logger)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(getattr(signal, signal_name), stop_event.set)
    try:
        await stop_event.wait()
    finally:
        await bridge.close()
        await runner.cleanup()


def parse_args() -> BridgeConfig:
    parser = argparse.ArgumentParser(description="Bridge MaiBot Live2D JSON events to VTube Studio.")
    parser.add_argument("--listen-host", default=DEFAULT_LISTEN_HOST)
    parser.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT)
    parser.add_argument("--listen-path", default=DEFAULT_LISTEN_PATH)
    parser.add_argument("--vts-url", default=DEFAULT_VTS_URL)
    parser.add_argument("--plugin-name", default=DEFAULT_PLUGIN_NAME)
    parser.add_argument("--plugin-developer", default=DEFAULT_PLUGIN_DEVELOPER)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    parser.add_argument("--mapping-file", type=Path, default=None)
    parser.add_argument("--no-create-custom-parameters", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    return BridgeConfig(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        listen_path=args.listen_path,
        vts_url=args.vts_url,
        plugin_name=args.plugin_name,
        plugin_developer=args.plugin_developer,
        token_file=args.token_file,
        mapping_file=args.mapping_file,
        create_custom_parameters=not args.no_create_custom_parameters,
        dry_run=args.dry_run,
    )


def _warn_if_ports_overlap(config: BridgeConfig, logger: logging.Logger) -> None:
    parsed = urlparse(config.vts_url)
    vts_port = parsed.port
    if vts_port == config.listen_port:
        logger.warning(
            "VTS API URL (%s) uses the same port as this bridge listener (%s). "
            "Use different ports, for example VTS on 8002 and bridge on 18081.",
            config.vts_url,
            config.listen_port,
        )


def load_mapping_file(path: Path | None) -> dict[str, list[str]]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("mapping file must contain a JSON object")
    mapping: dict[str, list[str]] = {}
    for key, value in raw.items():
        parameter_id = str(key).strip()
        if isinstance(value, str):
            targets = [value.strip()]
        elif isinstance(value, list):
            targets = [str(item).strip() for item in value]
        else:
            targets = []
        mapping[parameter_id] = [target for target in targets if target]
    return mapping


def merge_parameter_mapping(overrides: Mapping[str, list[str]] | None = None) -> dict[str, list[str]]:
    merged = {key: list(value) for key, value in DEFAULT_PARAMETER_MAPPING.items()}
    for key, value in dict(overrides or {}).items():
        merged[str(key)] = [str(item) for item in value]
    return merged


def convert_vts_live2d_parameter(raw_parameter: Mapping[str, Any]) -> dict[str, float | str]:
    return {
        "id": str(raw_parameter.get("name") or raw_parameter.get("id") or ""),
        "min": _as_float(raw_parameter.get("min"), default=-1.0),
        "max": _as_float(raw_parameter.get("max"), default=1.0),
        "default": _as_float(raw_parameter.get("defaultValue", raw_parameter.get("default")), default=0.0),
        "current": _as_float(raw_parameter.get("value", raw_parameter.get("current")), default=0.0),
    }


def infer_groups(parameters: list[Mapping[str, Any]]) -> dict[str, list[str]]:
    ids = {str(item.get("id") or "") for item in parameters}
    groups: dict[str, list[str]] = {}
    eye_blink = [parameter_id for parameter_id in ("ParamEyeLOpen", "ParamEyeROpen") if parameter_id in ids]
    lip_sync = [parameter_id for parameter_id in ("ParamMouthOpenY",) if parameter_id in ids]
    if eye_blink:
        groups["EyeBlink"] = eye_blink
    if lip_sync:
        groups["LipSync"] = lip_sync
    return groups


def select_motion_hotkey(
    hotkeys: list[Any],
    *,
    motion_name: str,
    motion_file: str,
    model_name: str = "",
) -> Mapping[str, Any] | None:
    """Select the best VTS hotkey for a Live2D motion request."""

    best_hotkey: Mapping[str, Any] | None = None
    best_score = 0
    normalized_motion = _normalize_match_token(motion_name)
    normalized_file = _normalize_match_text(motion_file)
    normalized_model = _normalize_match_token(model_name)
    for raw_hotkey in hotkeys:
        if not isinstance(raw_hotkey, Mapping):
            continue
        hotkey_type = str(raw_hotkey.get("type") or "").strip()
        if hotkey_type not in MOTION_HOTKEY_TYPES:
            continue
        score = MOTION_IDLE_TYPE_BONUS.get(hotkey_type, 0)
        hotkey_file = _normalize_match_text(raw_hotkey.get("file"))
        hotkey_name = _normalize_match_text(raw_hotkey.get("name"))
        hotkey_id = _normalize_match_text(raw_hotkey.get("hotkeyID") or raw_hotkey.get("id"))
        matched = False
        if normalized_file and hotkey_file == normalized_file:
            score += 100
            matched = True
        elif normalized_file and hotkey_file.endswith(normalized_file):
            score += 80
            matched = True
        if normalized_motion:
            motion_token = _normalize_match_token(normalized_motion)
            if motion_token and motion_token in _normalize_match_token(hotkey_file):
                score += 35
                matched = True
            if motion_token and motion_token in _normalize_match_token(hotkey_name):
                score += 25
                matched = True
            if motion_token and motion_token in _normalize_match_token(hotkey_id):
                score += 20
                matched = True
        if not matched:
            continue
        if normalized_model:
            searchable = _normalize_match_token(f"{hotkey_name} {hotkey_file} {hotkey_id}")
            if normalized_model in searchable:
                score += 5
        if score > best_score:
            best_score = score
            best_hotkey = raw_hotkey
    return best_hotkey


def interpolate_timeline_keyframes(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    interval_ms: int = TIMELINE_INTERPOLATION_INTERVAL_MS,
) -> list[dict[str, Any]]:
    """Create bridge-side transition frames between two Live2D timeline keyframes."""

    previous_offset = max(0, int(_as_float(previous.get("offset_ms"), default=0.0)))
    current_offset = max(0, int(_as_float(current.get("offset_ms"), default=0.0)))
    if current_offset <= previous_offset:
        payload = dict(current)
        payload["keyframe"] = True
        return [payload]
    interval = max(10, int(interval_ms))
    previous_parameters = _parameter_map(previous.get("parameters"))
    current_parameters = _parameter_map(current.get("parameters"))
    if not current_parameters:
        payload = dict(current)
        payload["keyframe"] = True
        return [payload]
    frames: list[dict[str, Any]] = []
    for offset_ms in range(previous_offset + interval, current_offset, interval):
        progress = (offset_ms - previous_offset) / max(1, current_offset - previous_offset)
        frames.append(
            _interpolated_timeline_payload(
                current,
                previous_parameters,
                current_parameters,
                offset_ms=offset_ms,
                factor=_smoothstep(progress),
                keyframe=False,
            )
        )
    frames.append(
        _interpolated_timeline_payload(
            current,
            previous_parameters,
            current_parameters,
            offset_ms=current_offset,
            factor=1.0,
            keyframe=True,
        )
    )
    return frames


def _interpolated_timeline_payload(
    template: Mapping[str, Any],
    previous_parameters: Mapping[str, Mapping[str, Any]],
    current_parameters: Mapping[str, Mapping[str, Any]],
    *,
    offset_ms: int,
    factor: float,
    keyframe: bool,
) -> dict[str, Any]:
    payload = dict(template)
    payload["offset_ms"] = max(0, int(offset_ms))
    payload["interpolated"] = True
    payload["keyframe"] = bool(keyframe)
    parameters: list[dict[str, float | str]] = []
    for parameter_id, current in current_parameters.items():
        previous = previous_parameters.get(parameter_id)
        start_value = _as_float(previous.get("value"), default=_as_float(current.get("value"))) if previous else _as_float(current.get("value"))
        end_value = _as_float(current.get("value"))
        start_weight = _as_float(previous.get("weight"), default=_as_float(current.get("weight"), default=1.0)) if previous else _as_float(current.get("weight"), default=1.0)
        end_weight = _as_float(current.get("weight"), default=1.0)
        parameters.append(
            {
                "id": parameter_id,
                "value": start_value + (end_value - start_value) * factor,
                "weight": min(1.0, max(0.0, start_weight + (end_weight - start_weight) * factor)),
            }
        )
    payload["parameters"] = parameters
    return payload


def _parameter_map(raw_parameters: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(raw_parameters, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for raw_parameter in raw_parameters:
        if not isinstance(raw_parameter, Mapping):
            continue
        parameter_id = str(raw_parameter.get("id") or "").strip()
        if parameter_id:
            result[parameter_id] = dict(raw_parameter)
    return result


def _smoothstep(value: float) -> float:
    clamped = min(1.0, max(0.0, float(value)))
    return clamped * clamped * (3.0 - 2.0 * clamped)


def _normalize_match_text(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/").lower()
    return text.rsplit("/", 1)[-1]


def _normalize_match_token(value: Any) -> str:
    return "".join(char for char in _normalize_match_text(value) if char.isalnum())


def sanitize_vts_parameter_name(parameter_id: str) -> str:
    allowed = set(string.ascii_letters + string.digits)
    sanitized = "".join(char for char in str(parameter_id) if char in allowed)
    if 4 <= len(sanitized) <= 32:
        return sanitized
    digest = hashlib.sha1(str(parameter_id).encode("utf-8")).hexdigest()[:8]
    prefix = "MB"
    trimmed = sanitized[: 32 - len(prefix) - len(digest)]
    return f"{prefix}{trimmed}{digest}"[:32]


def is_forbidden_parameter_id(parameter_id: str) -> bool:
    lowered = parameter_id.lower()
    return any(token in lowered for token in FORBIDDEN_ID_TOKENS)


def default_spec_for(parameter_id: str) -> dict[str, float | str]:
    for spec in FALLBACK_LIVE2D_PARAMETERS:
        if spec["id"] == parameter_id:
            return dict(spec)
    return {"id": parameter_id, "min": -1.0, "max": 1.0, "default": 0.0, "current": 0.0}


def _response_data(response: Mapping[str, Any]) -> Mapping[str, Any]:
    data = response.get("data")
    return data if isinstance(data, Mapping) else {}


def _as_float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def main() -> None:
    config = parse_args()
    try:
        asyncio.run(run_bridge(config))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
