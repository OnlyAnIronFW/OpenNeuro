"""配置管理器 — 加载/热更新/回滚"""

import hashlib
import os
from pathlib import Path
from typing import Callable, List, Optional

import yaml

from .schema import AppConfig


def _dict_to_config(data: dict) -> AppConfig:
    """递归将扁平字典映射到 AppConfig 数据类"""
    c = AppConfig()

    if "models" in data:
        m = data["models"]
        if "s1" in m:
            s1 = m["s1"]
            c.s1_model.model_id = s1.get("model_id", c.s1_model.model_id)
            c.s1_model.quantization = s1.get("quantization", c.s1_model.quantization)
            c.s1_model.device = s1.get("device", c.s1_model.device)
            if "inference" in s1:
                inf = s1["inference"]
                c.s1_model.max_tokens = inf.get("max_tokens", c.s1_model.max_tokens)
                c.s1_model.temperature = inf.get("temperature", c.s1_model.temperature)
                c.s1_model.decision_interval_ms = inf.get(
                    "decision_interval_ms", c.s1_model.decision_interval_ms
                )
        if "s2" in m:
            s2 = m["s2"]
            if "primary" in s2:
                p = s2["primary"]
                c.s2_model.provider = p.get("provider", c.s2_model.provider)
                c.s2_model.model = p.get("model", c.s2_model.model)
                c.s2_model.api_key = p.get("api_key", c.s2_model.api_key)
                c.s2_model.api_base = p.get("api_base", c.s2_model.api_base)
                c.s2_model.temperature = p.get("temperature", c.s2_model.temperature)
                c.s2_model.top_p = p.get("top_p", c.s2_model.top_p)
            c.s2_model.timeout_ms = s2.get("timeout_ms", c.s2_model.timeout_ms)

    if "s1_decision" in data:
        d = data["s1_decision"]
        c.s1_decision.protection_period_ms = d.get(
            "protection_period_ms", c.s1_decision.protection_period_ms
        )
        c.s1_decision.max_replies_per_10s = d.get(
            "max_replies_per_10s", c.s1_decision.max_replies_per_10s
        )
        c.s1_decision.silence_watchdog_ms = d.get(
            "silence_watchdog_ms", c.s1_decision.silence_watchdog_ms
        )
        c.s1_decision.forced_cooldown_ms = d.get(
            "forced_cooldown_ms", c.s1_decision.forced_cooldown_ms
        )
        c.s1_decision.speak_priority_threshold = d.get(
            "speak_priority_threshold", c.s1_decision.speak_priority_threshold
        )
        c.s1_decision.quick_reply_max_chars = d.get(
            "quick_reply_max_chars", c.s1_decision.quick_reply_max_chars
        )

    if "threads" in data:
        t = data["threads"]
        c.threads.max_active = t.get("max_active", c.threads.max_active)
        c.threads.merge_similarity_threshold = t.get(
            "merge_similarity_threshold", c.threads.merge_similarity_threshold
        )
        c.threads.cooldown_after_replies = t.get(
            "cooldown_after_replies", c.threads.cooldown_after_replies
        )
        c.threads.cooldown_duration_ms = t.get(
            "cooldown_duration_ms", c.threads.cooldown_duration_ms
        )
        c.threads.stale_timeout_ms = t.get(
            "stale_timeout_ms", c.threads.stale_timeout_ms
        )
        c.threads.close_timeout_ms = t.get(
            "close_timeout_ms", c.threads.close_timeout_ms
        )

    if "visual" in data:
        v = data["visual"]
        c.visual.capture_fps = v.get("capture_fps", c.visual.capture_fps)
        c.visual.resolution = v.get("resolution", c.visual.resolution)
        c.visual.change_detection_threshold = v.get(
            "change_detection_threshold", c.visual.change_detection_threshold
        )
        c.visual.heartbeat_timeout_ms = v.get(
            "heartbeat_timeout_ms", c.visual.heartbeat_timeout_ms
        )

    if "memory" in data:
        mem = data["memory"]
        if "l1" in mem:
            l1 = mem["l1"]
            c.memory.l1.recent_messages_count = l1.get(
                "recent_messages_count", c.memory.l1.recent_messages_count
            )
            c.memory.l1.recent_decisions_count = l1.get(
                "recent_decisions_count", c.memory.l1.recent_decisions_count
            )
        if "l2" in mem:
            l2 = mem["l2"]
            c.memory.l2.backend = l2.get("backend", c.memory.l2.backend)
            c.memory.l2.retention_days = l2.get(
                "retention_days", c.memory.l2.retention_days
            )
            if "semantic_cache" in l2:
                sc = l2["semantic_cache"]
                c.memory.l2.semantic_cache.enabled = sc.get("enabled", True)
                c.memory.l2.semantic_cache.similarity_threshold = sc.get(
                    "similarity_threshold", 0.88
                )
                c.memory.l2.semantic_cache.ttl_hours = sc.get("ttl_hours", 24)
        if "l3" in mem:
            l3 = mem["l3"]
            c.memory.l3.backend = l3.get("backend", c.memory.l3.backend)
            c.memory.l3.viewer_min_interactions = l3.get("viewer_min_interactions", 3)

    if "self_iteration" in data:
        si = data["self_iteration"]
        if "phase2_3" in si:
            p23 = si["phase2_3"]
            c.self_iteration.phase2_3.trigger = p23.get("trigger", "stream_end")
            c.self_iteration.phase2_3.max_samples = p23.get("max_samples", 500)
        if "phase4" in si:
            p4 = si["phase4"]
            c.self_iteration.phase4.trigger = p4.get("trigger", "weekly")
            c.self_iteration.phase4.require_human_approval = p4.get(
                "require_human_approval", True
            )
            c.self_iteration.phase4.require_replay_validation = p4.get(
                "require_replay_validation", True
            )
            c.self_iteration.phase4.replay_streams = p4.get("replay_streams", 3)
            c.self_iteration.phase4.min_score_improvement = p4.get(
                "min_score_improvement", 0.05
            )

    if "degradation" in data:
        dg = data["degradation"]
        c.degradation.s2_timeout_threshold_ms = dg.get(
            "s2_timeout_threshold_ms", c.degradation.s2_timeout_threshold_ms
        )
        c.degradation.s2_error_rate_threshold = dg.get(
            "s2_error_rate_threshold", c.degradation.s2_error_rate_threshold
        )
        c.degradation.auto_recovery_check_ms = dg.get(
            "auto_recovery_check_ms", c.degradation.auto_recovery_check_ms
        )
        c.degradation.recovery_min_success_streak = dg.get(
            "recovery_min_success_streak", c.degradation.recovery_min_success_streak
        )

    if "platforms" in data:
        plat = data["platforms"]
        for name in ["bilibili", "twitch", "discord"]:
            if name in plat:
                entry = getattr(c.platforms, name)
                entry.enabled = plat[name].get("enabled", entry.enabled)
                entry.output_filter = plat[name].get(
                    "output_filter", entry.output_filter
                )

    if "observability" in data:
        obs = data["observability"]
        c.observability.metrics_export_interval_ms = obs.get(
            "metrics_export_interval_ms", c.observability.metrics_export_interval_ms
        )
        c.observability.p0_notify_webhook = obs.get("p0_notify_webhook", "")
        c.observability.p1_notify_webhook = obs.get("p1_notify_webhook", "")
        c.observability.log_level = obs.get("log_level", "info")
        c.observability.decision_trace = obs.get("decision_trace", True)

    if "session" in data:
        s = data["session"]
        c.session.auto_save_recording = s.get("auto_save_recording", True)
        c.session.recording_path = s.get("recording_path", "data/recordings")
        c.session.generate_post_stream_report = s.get(
            "generate_post_stream_report", True
        )

    return c


class ConfigManager:
    """配置管理器 — 支持加载、热更新、change watchers"""

    def __init__(self, config_path: str = "config.yaml"):
        self._path = Path(config_path)
        self._config: Optional[AppConfig] = None
        self._hash: str = ""
        self._watchers: List[Callable[[AppConfig], None]] = []

    def load(self) -> AppConfig:
        """加载并解析 config.yaml"""
        if not self._path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self._path}")

        raw_text = self._path.read_text(encoding="utf-8")
        # 替换环境变量
        raw_text = self._resolve_env_vars(raw_text)
        raw = yaml.safe_load(raw_text)
        self._config = _dict_to_config(raw or {})
        _validate_config(self._config)
        self._hash = self._compute_hash()
        return self._config

    @property
    def current(self) -> AppConfig:
        if self._config is None:
            raise RuntimeError("配置未加载，请先调用 load()")
        return self._config

    def on_change(self, callback: Callable[[AppConfig], None]) -> None:
        """注册配置变更回调 (用于热更新)"""
        self._watchers.append(callback)

    def check_and_reload(self) -> bool:
        """检查文件变更并按需重载。返回 True 表示已重载。"""
        if not self._path.exists():
            return False
        new_text = self._path.read_text(encoding="utf-8")
        new_hash = hashlib.md5(new_text.encode()).hexdigest()
        if new_hash == self._hash:
            return False

        self.load()
        for w in self._watchers:
            try:
                w(self._config)
            except Exception:
                pass
        return True

    # ── 内部 ─────────────────────────────────────────

    @staticmethod
    def _resolve_env_vars(text: str) -> str:
        import re

        def replacer(match):
            var = match.group(1)
            return os.environ.get(var, match.group(0))

        return re.sub(r"\$\{(\w+)\}", replacer, text)

    def _compute_hash(self) -> str:
        return hashlib.md5(self._path.read_bytes()).hexdigest()


def _validate_config(cfg):
    """在 _dict_to_config 字段赋值后重新触发 __post_init__ 校验"""
    cfg.s2_model.__post_init__()
    # 以后的 Schema 增补校验也在此统一调用
