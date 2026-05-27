"""L3 长期记忆 — 跨会话持久化 + Ebbinghaus遗忘曲线 + 关系图谱"""

import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple


@dataclass
class LongTermViewer:
    user_id: str
    display_name: str = ""
    platform: str = ""
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    total_interactions: int = 0
    loyalty_level: int = 0
    # 关系图谱
    related_viewers: Dict[str, float] = field(
        default_factory=dict
    )  # user_id → 关联强度
    # 已知事实
    known_facts: Dict[str, str] = field(default_factory=dict)
    # 互动风格
    interaction_style: str = ""
    # 话题历史
    topics: List[str] = field(default_factory=list)
    # 跨会话记忆
    memorable_moments: List[Dict] = field(default_factory=list)
    notes: str = ""


class LongTermMemory:
    """
    L3 长期记忆 (Phase 5: 文件持久化, 后续升级 pgvector+Neo4j)

    特性:
      - Ebbinghaus遗忘曲线: 不活跃的观众自然降权
      - 关系图谱: 观众之间的互动关联
      - 跨会话记忆: 高光时刻永久保留
    """

    # 活跃度分级
    ACTIVE_THRESHOLD = 7 * 86400  # 7天
    DORMANT_THRESHOLD = 30 * 86400  # 30天
    SLEEPING_THRESHOLD = 90 * 86400  # 90天

    def __init__(self, data_dir: str = "data/memory"):
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._viewers: Dict[str, LongTermViewer] = {}
        self._path = self._dir / "l3_viewers.json"
        self._load()

    # ── 观众档案 ──────────────────────────────────────

    def get_viewer(self, user_id: str) -> Optional[LongTermViewer]:
        v = self._viewers.get(user_id)
        if v:
            v.last_seen = time.time()
            v.total_interactions += 1
            # 忠诚度公式与 memory_manager.ViewerProfile 保持一致: 每10次互动升一级
            v.loyalty_level = min(3, v.total_interactions // 10)
        return v

    def upsert_viewer(
        self, user_id: str, display_name: str = "", platform: str = ""
    ) -> LongTermViewer:
        if user_id in self._viewers:
            v = self._viewers[user_id]
        else:
            v = LongTermViewer(user_id=user_id)
            self._viewers[user_id] = v

        if display_name:
            v.display_name = display_name
        if platform:
            v.platform = platform
        v.last_seen = time.time()
        v.total_interactions += 1
        # 忠诚度公式: 每10次互动升一级 (memory_manager.ViewerProfile 为规范版本)
        v.loyalty_level = min(3, v.total_interactions // 10)
        return v

    # ── Ebbinghaus 遗忘曲线 ───────────────────────────

    def get_retrieval_weight(self, user_id: str) -> float:
        """计算观众的记忆检索权重 (遗忘曲线)"""
        v = self._viewers.get(user_id)
        if not v:
            return 0.0

        days_since = (time.time() - v.last_seen) / 86400

        if days_since < 1:
            return 1.0
        elif days_since < self.ACTIVE_THRESHOLD / 86400:
            return 0.8
        elif days_since < self.DORMANT_THRESHOLD / 86400:
            return 0.5
        elif days_since < self.SLEEPING_THRESHOLD / 86400:
            return 0.2
        else:
            return 0.05  # >90天几乎遗忘

    def get_activity_level(self, user_id: str) -> str:
        """返回活跃度标签"""
        days = (
            time.time() - (self._viewers.get(user_id, LongTermViewer("")).last_seen)
        ) / 86400
        if days < 7:
            return "活跃"
        if days < 30:
            return "休眠"
        if days < 90:
            return "沉睡"
        return "流失"

    # ── 关系图谱 ──────────────────────────────────────

    def record_interaction_between(self, user_a: str, user_b: str) -> None:
        """记录两个观众之间的互动"""
        for uid in (user_a, user_b):
            if uid not in self._viewers:
                self.upsert_viewer(uid)

        va = self._viewers[user_a]
        vb = self._viewers[user_b]
        va.related_viewers[user_b] = va.related_viewers.get(user_b, 0) + 0.5
        vb.related_viewers[user_a] = vb.related_viewers.get(user_a, 0) + 0.5

    def get_related_viewers(
        self, user_id: str, min_strength: float = 0.3
    ) -> List[Tuple[str, float]]:
        v = self._viewers.get(user_id)
        if not v:
            return []
        return [(uid, s) for uid, s in v.related_viewers.items() if s >= min_strength]

    # ── 高光时刻 ──────────────────────────────────────

    def record_moment(self, user_id: str, moment: str, score: float = 5.0) -> None:
        if user_id not in self._viewers:
            self.upsert_viewer(user_id)
        v = self._viewers[user_id]
        v.memorable_moments.append(
            {
                "moment": moment,
                "score": score,
                "timestamp": time.time(),
            }
        )
        if len(v.memorable_moments) > 20:
            v.memorable_moments = sorted(
                v.memorable_moments, key=lambda m: m["score"], reverse=True
            )[:15]

    # ── 上下文注入 ────────────────────────────────────

    def get_viewer_context(self, user_id: str) -> str:
        v = self._viewers.get(user_id)
        if not v:
            return ""

        weight = self.get_retrieval_weight(user_id)
        if weight < 0.1:
            return ""  # 几乎遗忘的观众不注入上下文

        level_label = self.get_activity_level(user_id)
        parts = [f"{v.display_name or user_id}({level_label}·Lv{v.loyalty_level})"]

        if v.total_interactions > 5:
            parts.append(f"互动{v.total_interactions}次")
        if v.known_facts:
            facts = [f"{k}={val}" for k, val in list(v.known_facts.items())[:3]]
            parts.append(f"已知:{', '.join(facts)}")
        if v.interaction_style:
            parts.append(v.interaction_style)
        if v.memorable_moments:
            top = sorted(v.memorable_moments, key=lambda m: m["score"], reverse=True)[
                :2
            ]
            parts.append(f"记忆:{'; '.join(m['moment'][:20] for m in top)}")

        # 关系图谱
        related = self.get_related_viewers(user_id)
        if related:
            names = [
                self._viewers[uid].display_name or uid
                for uid, _ in related[:3]
                if uid in self._viewers
            ]
            if names:
                parts.append(f"常互动:{', '.join(names)}")

        return " | ".join(parts)

    # ── 持久化 ────────────────────────────────────────

    def save(self) -> None:
        data = {}
        for uid, v in self._viewers.items():
            data[uid] = {
                "user_id": v.user_id,
                "display_name": v.display_name,
                "platform": v.platform,
                "first_seen": v.first_seen,
                "last_seen": v.last_seen,
                "total_interactions": v.total_interactions,
                "loyalty_level": v.loyalty_level,
                "related_viewers": v.related_viewers,
                "known_facts": v.known_facts,
                "interaction_style": v.interaction_style,
                "topics": v.topics,
                "memorable_moments": v.memorable_moments,
                "notes": v.notes,
            }
        try:
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for uid, vd in data.items():
                self._viewers[uid] = LongTermViewer(
                    user_id=vd.get("user_id", uid),
                    display_name=vd.get("display_name", ""),
                    platform=vd.get("platform", ""),
                    first_seen=vd.get("first_seen", 0),
                    last_seen=vd.get("last_seen", 0),
                    total_interactions=vd.get("total_interactions", 0),
                    loyalty_level=vd.get("loyalty_level", 0),
                    related_viewers=vd.get("related_viewers", {}),
                    known_facts=vd.get("known_facts", {}),
                    interaction_style=vd.get("interaction_style", ""),
                    topics=vd.get("topics", []),
                    memorable_moments=vd.get("memorable_moments", []),
                    notes=vd.get("notes", ""),
                )
        except (json.JSONDecodeError, OSError):
            pass

    @property
    def viewer_count(self) -> int:
        return len(self._viewers)

    def get_active_viewers(self, limit: int = 20) -> List[LongTermViewer]:
        viewers = [
            (v, self.get_retrieval_weight(uid)) for uid, v in self._viewers.items()
        ]
        viewers.sort(key=lambda x: -x[1])
        return [v for v, _ in viewers[:limit]]
