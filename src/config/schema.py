"""配置数据模型 — 类型安全的配置访问"""

from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class S1ModelConfig:
    model_id: str = "openbmb/MiniCPM-o-4_5"
    quantization: str = "int4"
    device: str = "cuda:0"
    max_tokens: int = 128
    temperature: float = 0.1
    decision_interval_ms: int = 1000


@dataclass
class S2ModelConfig:
    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    api_key: str = ""
    api_base: str = "https://api.deepseek.com/v1"
    temperature: float = 1.0
    top_p: float = 1.0
    timeout_ms: int = 5000

    def __post_init__(self):
        if self.temperature < 0 or self.temperature > 2:
            raise ValueError(
                f"S2ModelConfig.temperature must be in [0, 2], got {self.temperature}"
            )
        if self.top_p < 0 or self.top_p > 1:
            raise ValueError(f"S2ModelConfig.top_p must be in [0, 1], got {self.top_p}")
        if self.timeout_ms < 500:
            raise ValueError(
                f"S2ModelConfig.timeout_ms must be >= 500, got {self.timeout_ms}"
            )


@dataclass
class S1DecisionConfig:
    protection_period_ms: int = 2000
    max_replies_per_10s: int = 3
    silence_watchdog_ms: int = 60000
    forced_cooldown_ms: int = 5000
    speak_priority_threshold: float = 0.5
    quick_reply_max_chars: int = 15


@dataclass
class ThreadConfig:
    max_active: int = 10
    merge_similarity_threshold: float = 0.7
    cooldown_after_replies: int = 3
    cooldown_duration_ms: int = 10000
    stale_timeout_ms: int = 300000
    close_timeout_ms: int = 900000


@dataclass
class VisualConfig:
    capture_fps: int = 2
    resolution: int = 512
    change_detection_threshold: float = 0.05
    heartbeat_timeout_ms: int = 5000


@dataclass
class MemoryL1Config:
    recent_messages_count: int = 50
    recent_decisions_count: int = 10


@dataclass
class SemanticCacheConfig:
    enabled: bool = True
    similarity_threshold: float = 0.88
    ttl_hours: int = 24


@dataclass
class MemoryL2Config:
    backend: str = "chromadb"
    retention_days: int = 30
    semantic_cache: SemanticCacheConfig = field(default_factory=SemanticCacheConfig)


@dataclass
class MemoryL3Config:
    backend: str = "pgvector"
    viewer_min_interactions: int = 3


@dataclass
class MemoryConfig:
    l1: MemoryL1Config = field(default_factory=MemoryL1Config)
    l2: MemoryL2Config = field(default_factory=MemoryL2Config)
    l3: MemoryL3Config = field(default_factory=MemoryL3Config)


@dataclass
class SelfIterationPhase23Config:
    trigger: str = "stream_end"
    max_samples: int = 500


@dataclass
class SelfIterationPhase4Config:
    trigger: str = "weekly"
    require_human_approval: bool = True
    require_replay_validation: bool = True
    replay_streams: int = 3
    min_score_improvement: float = 0.05


@dataclass
class SelfIterationConfig:
    phase2_3: SelfIterationPhase23Config = field(
        default_factory=SelfIterationPhase23Config
    )
    phase4: SelfIterationPhase4Config = field(default_factory=SelfIterationPhase4Config)


@dataclass
class DegradationConfig:
    s2_timeout_threshold_ms: int = 3000
    s2_error_rate_threshold: float = 0.3
    auto_recovery_check_ms: int = 30000
    recovery_min_success_streak: int = 10


@dataclass
class PlatformEntry:
    enabled: bool = False
    output_filter: str = "lenient"


@dataclass
class PlatformsConfig:
    bilibili: PlatformEntry = field(
        default_factory=lambda: PlatformEntry(enabled=True, output_filter="strict")
    )
    twitch: PlatformEntry = field(default_factory=PlatformEntry)
    discord: PlatformEntry = field(default_factory=PlatformEntry)


@dataclass
class ObservabilityConfig:
    metrics_export_interval_ms: int = 10000
    p0_notify_webhook: str = ""
    p1_notify_webhook: str = ""
    log_level: str = "info"
    decision_trace: bool = True


@dataclass
class SessionConfig:
    auto_save_recording: bool = True
    recording_path: str = "data/recordings"
    generate_post_stream_report: bool = True


@dataclass
class AppConfig:
    """应用主配置"""

    s1_model: S1ModelConfig = field(default_factory=S1ModelConfig)
    s2_model: S2ModelConfig = field(default_factory=S2ModelConfig)
    s1_decision: S1DecisionConfig = field(default_factory=S1DecisionConfig)
    threads: ThreadConfig = field(default_factory=ThreadConfig)
    visual: VisualConfig = field(default_factory=VisualConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    self_iteration: SelfIterationConfig = field(default_factory=SelfIterationConfig)
    degradation: DegradationConfig = field(default_factory=DegradationConfig)
    platforms: PlatformsConfig = field(default_factory=PlatformsConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
