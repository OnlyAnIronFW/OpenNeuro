"""Configuration loading for the standalone live hub."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback.
    import tomli as tomllib  # type: ignore[no-redef]

from plugins.bilibili_live_adapter.config import BilibiliConfig, LiveAdapterSettings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "live_hub.toml"
DEFAULT_SOURCE_ADAPTER_CONFIG = (
    PROJECT_ROOT / "plugins" / "bilibili_live_adapter" / "config.toml"
)


def _normalize_text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _normalize_positive_int(value: Any, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


def _normalize_boolean(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(slots=True)
class HubServerConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 18190
    title: str = "Live Hub"
    recent_events_limit: int = 400
    history_snapshot_limit: int = 200
    preload_history_on_startup: bool = True
    preload_history_limit: int = 20


@dataclass(slots=True)
class LocalInputConfig:
    enabled: bool = True
    default_username: str = "Hub Local"
    default_user_id: str = "hub-local"


@dataclass(slots=True)
class ClientApiConfig:
    enabled: bool = True
    presence_ttl_sec: int = 30


@dataclass(slots=True)
class SpeechCoordinationConfig:
    enabled: bool = True
    stale_speaker_timeout_sec: int = 180


@dataclass(slots=True)
class ClientIdentityMapping:
    forward_user_id: str = ""
    forward_username: str = ""


@dataclass(slots=True)
class LiveHubSettings:
    bilibili: BilibiliConfig = field(default_factory=BilibiliConfig)
    hub: HubServerConfig = field(default_factory=HubServerConfig)
    input: LocalInputConfig = field(default_factory=LocalInputConfig)
    client_api: ClientApiConfig = field(default_factory=ClientApiConfig)
    speech: SpeechCoordinationConfig = field(default_factory=SpeechCoordinationConfig)
    client_mappings: dict[str, ClientIdentityMapping] = field(default_factory=dict)
    source_adapter_config: Path = DEFAULT_SOURCE_ADAPTER_CONFIG

    def with_runtime_overrides(
        self,
        *,
        room_id: int = 0,
        listen_host: str = "",
        listen_port: int = 0,
    ) -> "LiveHubSettings":
        updated = self
        if room_id > 0:
            updated = replace(
                updated,
                bilibili=updated.bilibili.model_copy(update={"room_id": room_id}),
            )
        if listen_host or listen_port > 0:
            updated_hub = replace(
                updated.hub,
                listen_host=_normalize_text(listen_host, updated.hub.listen_host),
                listen_port=_normalize_positive_int(
                    listen_port, updated.hub.listen_port
                ),
            )
            updated = replace(updated, hub=updated_hub)
        return updated


def _read_toml(path: Path) -> dict[str, Any]:
    return dict(tomllib.loads(path.read_text(encoding="utf-8")))


def _resolve_path(candidate: str | Path | None, *, relative_to: Path) -> Path:
    raw = str(candidate or "").strip()
    if not raw:
        return DEFAULT_SOURCE_ADAPTER_CONFIG
    path = Path(raw)
    if not path.is_absolute():
        relative_candidate = (relative_to / path).resolve()
        if relative_candidate.exists():
            return relative_candidate
        project_candidate = (PROJECT_ROOT / path).resolve()
        if project_candidate.exists():
            return project_candidate
        path = relative_candidate
    return path


def _load_source_adapter_settings(path: Path) -> LiveAdapterSettings:
    raw = _read_toml(path)
    return LiveAdapterSettings.model_validate(raw)


def load_live_hub_settings(path: Path = DEFAULT_CONFIG_PATH) -> LiveHubSettings:
    config_path = Path(path).resolve()
    raw = _read_toml(config_path)
    hub_raw = dict(raw.get("hub") or {})
    input_raw = dict(raw.get("input") or {})
    client_api_raw = dict(raw.get("client_api") or {})
    speech_raw = dict(raw.get("speech") or {})
    bilibili_overrides = dict(raw.get("bilibili") or {})

    source_adapter_config = _resolve_path(
        hub_raw.get("source_adapter_config"), relative_to=config_path.parent
    )
    source_settings = _load_source_adapter_settings(source_adapter_config)
    base_bilibili = source_settings.bilibili
    if bilibili_overrides:
        base_bilibili = base_bilibili.model_copy(update=bilibili_overrides)

    hub = HubServerConfig(
        listen_host=_normalize_text(hub_raw.get("listen_host"), "127.0.0.1"),
        listen_port=_normalize_positive_int(hub_raw.get("listen_port"), 18190),
        title=_normalize_text(hub_raw.get("title"), "Live Hub"),
        recent_events_limit=_normalize_positive_int(
            hub_raw.get("recent_events_limit"), 400
        ),
        history_snapshot_limit=_normalize_positive_int(
            hub_raw.get("history_snapshot_limit"), 200
        ),
        preload_history_on_startup=_normalize_boolean(
            hub_raw.get("preload_history_on_startup"), True
        ),
        preload_history_limit=_normalize_positive_int(
            hub_raw.get("preload_history_limit"), 20
        ),
    )
    local_input = LocalInputConfig(
        enabled=_normalize_boolean(input_raw.get("enabled"), True),
        default_username=_normalize_text(
            input_raw.get("default_username"), "Hub Local"
        ),
        default_user_id=_normalize_text(input_raw.get("default_user_id"), "hub-local"),
    )
    client_api = ClientApiConfig(
        enabled=_normalize_boolean(client_api_raw.get("enabled"), True),
        presence_ttl_sec=_normalize_positive_int(
            client_api_raw.get("presence_ttl_sec"), 30
        ),
    )
    speech = SpeechCoordinationConfig(
        enabled=_normalize_boolean(speech_raw.get("enabled"), True),
        stale_speaker_timeout_sec=_normalize_positive_int(
            speech_raw.get("stale_speaker_timeout_sec"), 180
        ),
    )
    client_mappings_raw = dict(raw.get("clients") or {})
    client_mappings: dict[str, ClientIdentityMapping] = {}
    for key, value in client_mappings_raw.items():
        client_id = str(key or "").strip()
        if not client_id or not isinstance(value, Mapping):
            continue
        mapping = ClientIdentityMapping(
            forward_user_id=_normalize_text(value.get("forward_user_id"), ""),
            forward_username=_normalize_text(value.get("forward_username"), ""),
        )
        client_mappings[client_id] = mapping
    return LiveHubSettings(
        bilibili=base_bilibili,
        hub=hub,
        input=local_input,
        client_api=client_api,
        speech=speech,
        client_mappings=client_mappings,
        source_adapter_config=source_adapter_config,
    )
