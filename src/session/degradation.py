"""降级链 — L0-L4 自动降级 + 恢复"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, List, Dict


class DegradationLevel(Enum):
    L0 = 0  # 全能力: S1+S2 正常
    L1 = 1  # S2降级: 切换到更快的备用模型
    L2 = 2  # S2离线: S1承担所有回复
    L3 = 3  # S1降级: 关视觉, 降决策频率
    L4 = 4  # 人工接管: 预设回复


@dataclass
class DegradationState:
    level: DegradationLevel = DegradationLevel.L0
    since: float = field(default_factory=time.time)
    trigger_reason: str = ""
    s2_error_count: int = 0
    s2_success_count: int = 0
    s1_error_count: int = 0
    auto_recovery_attempts: int = 0


class DegradationManager:
    """自动降级管理"""

    def __init__(
        self,
        s2_error_threshold: int = 5,
        s2_recovery_streak: int = 10,
        s1_error_threshold: int = 3,
        recovery_check_interval_ms: int = 30000,
        immediate_recovery: bool = False,  # 测试用: 跳过恢复间隔
    ):
        self._state = DegradationState()
        self._s2_error_threshold = s2_error_threshold
        self._s2_recovery_streak = s2_recovery_streak
        self._s1_error_threshold = s1_error_threshold
        self._recovery_interval = (
            0 if immediate_recovery else recovery_check_interval_ms
        )
        self._handlers: Dict[DegradationLevel, List[Callable]] = {}
        self._last_recovery_check: float = 0.0

    # ── 回调 ───────────────────────────────────────────

    def on_level_change(
        self, level: DegradationLevel, handler: Callable[[DegradationLevel, str], None]
    ):
        if level not in self._handlers:
            self._handlers[level] = []
        self._handlers[level].append(handler)

    # ── S2 监控 ────────────────────────────────────────

    def record_s2_success(self) -> None:
        self._state.s2_success_count += 1
        self._state.s2_error_count = max(0, self._state.s2_error_count - 1)
        self._check_recovery()

    def record_s2_error(self, reason: str = "") -> Optional[DegradationLevel]:
        """记录S2错误, 返回是否需要降级"""
        self._state.s2_error_count += 1
        self._state.s2_success_count = 0

        if self._state.s2_error_count >= self._s2_error_threshold:
            if self._state.level == DegradationLevel.L0:
                return self._degrade(
                    DegradationLevel.L1,
                    f"S2连续{self._state.s2_error_count}次错误: {reason}",
                )
            elif self._state.level == DegradationLevel.L1:
                return self._degrade(
                    DegradationLevel.L2,
                    f"S2降级后仍{self._state.s2_error_count}次错误: {reason}",
                )
        return None

    # ── S1 监控 ────────────────────────────────────────

    def record_s1_error(self, reason: str = "") -> Optional[DegradationLevel]:
        self._state.s1_error_count += 1
        if self._state.s1_error_count >= self._s1_error_threshold:
            if self._state.level.value < 3:
                return self._degrade(
                    DegradationLevel.L3,
                    f"S1连续{self._state.s1_error_count}次错误: {reason}",
                )
        return None

    # ── 恢复检测 ───────────────────────────────────────

    def _check_recovery(self) -> Optional[DegradationLevel]:
        """检测是否满足恢复条件"""
        if self._state.level == DegradationLevel.L0:
            return None

        # 间隔检查 (避免频繁恢复)
        now = time.time()
        if (now - self._last_recovery_check) * 1000 < self._recovery_interval:
            return None
        self._last_recovery_check = now

        if self._state.s2_success_count >= self._s2_recovery_streak:
            return self._recover()

        return None

    def _degrade(self, new_level: DegradationLevel, reason: str) -> DegradationLevel:
        old = self._state.level
        self._state.level = new_level
        self._state.since = time.time()
        self._state.trigger_reason = reason
        self._fire_handlers(new_level, reason)
        return new_level

    def _recover(self) -> DegradationLevel:
        """恢复到L0"""
        self._state.level = DegradationLevel.L0
        self._state.s2_error_count = 0
        self._state.s2_success_count = 0
        self._state.auto_recovery_attempts += 1
        self._state.trigger_reason = "自动恢复"
        self._fire_handlers(DegradationLevel.L0, "自动恢复")
        return DegradationLevel.L0

    def force_degrade(self, level: DegradationLevel, reason: str) -> None:
        self._degrade(level, reason)

    def force_recover(self) -> None:
        self._recover()

    # ── 查询 ───────────────────────────────────────────

    @property
    def level(self) -> DegradationLevel:
        return self._state.level

    @property
    def state(self) -> DegradationState:
        return self._state

    def _fire_handlers(self, level: DegradationLevel, reason: str) -> None:
        for h in self._handlers.get(level, []):
            try:
                h(level, reason)
            except Exception as e:
                print(f"[Degradation] Handler error for level={level}: {e}")
