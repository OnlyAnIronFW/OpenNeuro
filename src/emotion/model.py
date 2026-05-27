"""情绪模型 — VAD三维 + 事件触发 + 自然衰减"""

import time
from dataclasses import dataclass, field
from typing import Dict, Tuple


# ── 事件→情绪映射表 ──────────────────────────────────

EMOTION_TRIGGERS: Dict[str, Tuple[float, float, float]] = {
    # (valence, arousal, dominance)
    "gift":            (0.15, 0.10, 0.00),
    "big_gift":        (0.30, 0.30, 0.00),
    "subscription":    (0.20, 0.15, 0.05),
    "compliment":      (0.20, 0.05, 0.10),
    "insult":          (-0.10, 0.10, -0.05),
    "game_win":        (0.40, 0.50, 0.30),
    "game_lose":       (-0.15, 0.05, -0.10),
    "rare_drop":       (0.30, 0.40, 0.00),
    "death":           (-0.10, 0.20, -0.10),
    "achievement":     (0.25, 0.30, 0.15),
    "vet_return":      (0.15, 0.05, 0.00),
    "new_viewer":      (0.05, 0.02, 0.02),
    "silence_1min":    (-0.05, -0.03, 0.00),
    "silence_5min":    (-0.10, -0.08, -0.05),
    "interaction_good":(0.15, 0.05, 0.10),
    "interaction_bad": (-0.10, 0.05, -0.05),
    "high_chat_velocity": (0.05, 0.15, 0.05),
    "boss_encounter":  (0.00, 0.30, -0.10),  # 紧张但不负面
    "cutscene":        (0.05, 0.05, 0.00),
}


@dataclass
class EmotionalState:
    """
    VAD 三维情绪模型:
      valence:  -1.0(极负面) ~ 1.0(极正面)
      arousal:   0.0(极平静) ~ 1.0(极激动)
      dominance: 0.0(完全无助) ~ 1.0(完全掌控)
    """
    valence: float = 0.10
    arousal: float = 0.25
    dominance: float = 0.55
    last_update: float = field(default_factory=time.time)

    # ── 事件触发 ──────────────────────────────────────

    def trigger(self, event_type: str) -> None:
        """应用情绪触发"""
        if event_type in EMOTION_TRIGGERS:
            v, a, d = EMOTION_TRIGGERS[event_type]
            self.apply(v, a, d)

    def apply(self, valence_delta: float = 0, arousal_delta: float = 0,
              dominance_delta: float = 0) -> None:
        self.valence = max(-1.0, min(1.0, self.valence + valence_delta))
        self.arousal = max(0.0, min(1.0, self.arousal + arousal_delta))
        self.dominance = max(0.0, min(1.0, self.dominance + dominance_delta))
        self.last_update = time.time()

    # ── 自然衰减 ──────────────────────────────────────

    def decay(self) -> None:
        """每5分钟衰减一次 (调用方控制频率)"""
        now = time.time()
        elapsed = now - self.last_update
        cycles = min(int(elapsed / 300), 12)  # 最多60分钟衰减

        for _ in range(cycles):
            self.valence *= 0.85       # → 0
            self.arousal = 0.25 + (self.arousal - 0.25) * 0.80  # → 0.25
            self.dominance = 0.55 + (self.dominance - 0.55) * 0.90  # → 0.55

        self.last_update = now

    # ── 查询 ──────────────────────────────────────────

    @property
    def is_excited(self) -> bool:
        return self.arousal > 0.6

    @property
    def is_upset(self) -> bool:
        return self.valence < -0.3

    @property
    def is_happy(self) -> bool:
        return self.valence > 0.4

    @property
    def is_bored(self) -> bool:
        return self.arousal < 0.15 and self.valence < 0.1

    @property
    def is_confident(self) -> bool:
        return self.dominance > 0.65

    # ── S1 影响 ───────────────────────────────────────

    @property
    def speak_threshold_modifier(self) -> float:
        """S1发言阈值修正: 开心→多说, 沮丧→少说"""
        if self.is_happy:
            return -0.15
        if self.is_upset:
            return 0.20
        if self.is_bored:
            return -0.10  # 无聊时也说 (主动找话题)
        return 0.0

    # ── S2 影响 ───────────────────────────────────────

    def to_prompt_str(self) -> str:
        """转为 S2 prompt 注入文本"""
        v_map = {True: "开心", False: "沮丧" if self.valence < -0.3 else "正常"}
        a_map = {True: "激动", False: "平静" if self.arousal < 0.2 else "正常"}
        d_map = {True: "自信", False: "没把握" if self.dominance < 0.3 else "正常"}

        v = "开心" if self.valence > 0.4 else ("沮丧" if self.valence < -0.3 else "正常")
        a = "激动" if self.arousal > 0.6 else ("平静" if self.arousal < 0.2 else "正常")
        return f"{v}, {a}"

    # ── 序列化 ────────────────────────────────────────

    def to_dict(self) -> Dict[str, float]:
        return {"valence": self.valence, "arousal": self.arousal, "dominance": self.dominance}

    def reset(self) -> None:
        self.valence = 0.10
        self.arousal = 0.25
        self.dominance = 0.55
        self.last_update = time.time()
