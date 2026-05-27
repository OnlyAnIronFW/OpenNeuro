"""Letta-like 三层记忆管理 — Core / Archival / Recall

纯 Python, 零 Docker。保留 L2+L3 全部功能, 增加自主记忆决策能力。

Core Memory:    当前会话上下文 (L1 快照 + 话题 + 情绪状态)
Archival Memory: 长期事实 (Graphiti 语义搜索 + Ebbinghaus 遗忘曲线过滤)
Recall Memory:  最近对话历史 (上下文窗口预算管理)

额外保留:
  - 观众档案 (L2 ViewerProfile)
  - FAQ 缓存 (语义匹配替代 Levenshtein)
  - Ebbinghaus 遗忘曲线 (L3)
  - 关系图谱 (L3)
  - 高光时刻 (L3)
"""

import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .graphiti_store import GraphitiStore


# ── 数据结构 ──────────────────────────────────────────


@dataclass
class ViewerProfile:
    user_id: str
    display_name: str = ""
    platform: str = ""
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    interaction_count: int = 0
    loyalty_level: int = 0  # 0=路人 1=常客 2=老粉 3=铁粉
    topics: list[str] = field(default_factory=list)
    known_facts: dict[str, str] = field(default_factory=dict)
    interaction_style: str = ""
    notes: str = ""


# ── MemoryManager ──────────────────────────────────────


class MemoryManager:
    """Letta-like 三层记忆管理 + 观众档案 + FAQ + 关系图谱"""

    # Ebbinghaus 遗忘曲线阈值
    ACTIVE_THRESHOLD = 7 * 86400
    DORMANT_THRESHOLD = 30 * 86400
    SLEEPING_THRESHOLD = 90 * 86400

    def __init__(
        self,
        graphiti: GraphitiStore,
        data_dir: str = "data/memory",
        l1=None,
    ):
        self._graphiti = graphiti
        self._wm = l1  # L1 WorkingMemory 引用

        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

        # 观众档案
        self._viewers: dict[str, ViewerProfile] = {}
        # FAQ 缓存: key → (reply, last_hit)
        self._faq: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._max_faq = 200
        # 关系图谱: user_a → {user_b: strength}
        self._relationships: dict[str, dict[str, float]] = {}
        # 高光时刻: user_id → [{moment, score, timestamp}]
        self._moments: dict[str, list[dict]] = {}
        # 最近互动记录 (Recall Memory)
        self._interactions: list[dict] = []
        self._max_interactions = 500

        self._load()

    # ── Core Memory (工作记忆层) ────────────────────────

    def snapshot_core(self) -> dict:
        """捕获 L1 快照 → S1 决策用"""
        if self._wm:
            return self._wm.to_context()
        return {}

    def inject_core_context(self) -> str:
        """Core Memory → S2 prompt 文本"""
        if not self._wm:
            return ""

        ctx = self._wm.to_context()
        parts = []
        if ctx.get("current_topic"):
            parts.append(f"当前话题: {ctx['current_topic']}")
        parts.append(f"距上次回复: {ctx.get('seconds_since_last_reply', 0):.0f}秒")
        parts.append(f"本场回复数: {ctx.get('reply_count', 0)}")
        if ctx.get("visual_summary"):
            parts.append(f"画面: {ctx['visual_summary'][:80]}")
        return "\n".join(parts)

    # ── Archival Memory (长期知识层) ────────────────────

    async def search_archival(
        self, query: str, user_id: str = "", min_weight: float = 0.1
    ) -> list[dict]:
        """Graphiti 语义搜索 + Ebbinghaus 遗忘曲线过滤"""
        results = await self._graphiti.search(query, user_id=user_id)
        if not results:
            return []

        filtered = []
        for r in results:
            uid = r.get("group_id", "")
            if self.retrieval_weight(uid) >= min_weight:
                r["_weight"] = self.retrieval_weight(uid)
                r["_activity"] = self.activity_level(uid)
                filtered.append(r)
        return filtered

    async def store_archival(
        self, user_id: str, display_name: str,
        message: str, reply: str, platform: str = "bilibili",
    ) -> bool:
        """异步写入 Graphiti (后台, 不阻塞回复)"""
        return await self._graphiti.add_interaction(
            user_id, display_name, message, reply, platform,
        )

    # ── Recall Memory (对话历史层) ──────────────────────

    def record_recall(self, query: str, reply: str, user_id: str,
                      s1_token: str = "", score: float = 0.0) -> None:
        """记录互动到 Recall Memory"""
        self._interactions.append({
            "query": query, "reply": reply, "user_id": user_id,
            "timestamp": time.time(), "s1_token": s1_token, "score": score,
        })
        if len(self._interactions) > self._max_interactions:
            self._interactions = self._interactions[-self._max_interactions:]

    def budget_context_window(self, max_tokens: int = 2000) -> str:
        """上下文窗口预算: 从 Recall 中选取最相关/最近的条目

        策略: 优先高分条目, 超出预算时从最旧开始裁剪。
        """
        if not self._interactions:
            return ""

        recent = self._interactions[-50:]
        recent.sort(key=lambda x: x.get("score", 0), reverse=True)

        lines = []
        char_budget = max_tokens * 2  # 粗略: 1 token ≈ 2 chars (中文)
        used = 0
        for entry in recent:
            line = f"Q: {entry['query'][:50]} → A: {entry['reply'][:50]}"
            if used + len(line) > char_budget:
                break
            lines.append(line)
            used += len(line)

        return "最近互动:\n" + "\n".join(lines) if lines else ""

    def get_recent_context(self, user_id: str = "", limit: int = 10) -> str:
        """获取最近互动上下文 (保留 L2 API)"""
        items = self._interactions[-50:]
        if user_id:
            items = [e for e in items if e.get("user_id") == user_id]
        if not items:
            return ""

        lines = []
        for e in items[-limit:]:
            lines.append(f"Q: {e['query'][:40]} → A: {e['reply'][:40]}")
        return "最近互动:\n" + "\n".join(lines)

    # ── Ebbinghaus 遗忘曲线 (保留 L3) ───────────────────

    def retrieval_weight(self, user_id: str) -> float:
        v = self._viewers.get(user_id)
        if not v:
            return 0.0
        days = (time.time() - v.last_seen) / 86400
        if days < 1:
            return 1.0
        elif days < 7:
            return 0.8
        elif days < 30:
            return 0.5
        elif days < 90:
            return 0.2
        return 0.05

    def activity_level(self, user_id: str) -> str:
        v = self._viewers.get(user_id)
        if not v:
            return "新观众"
        days = (time.time() - v.last_seen) / 86400
        if days < 7:
            return "活跃"
        if days < 30:
            return "休眠"
        if days < 90:
            return "沉睡"
        return "流失"

    # ── 观众档案 (保留 L2) ──────────────────────────────

    def get_viewer(self, user_id: str) -> ViewerProfile | None:
        return self._viewers.get(user_id)

    def upsert_viewer(self, user_id: str, display_name: str = "",
                      platform: str = "", **kwargs) -> ViewerProfile:
        if user_id in self._viewers:
            v = self._viewers[user_id]
        else:
            v = ViewerProfile(user_id=user_id, display_name=display_name)
            self._viewers[user_id] = v

        if display_name:
            v.display_name = display_name
        if platform:
            v.platform = platform
        v.last_seen = time.time()
        v.interaction_count += 1
        v.loyalty_level = min(3, v.interaction_count // 10)

        for k, val in kwargs.items():
            if hasattr(v, k):
                setattr(v, k, val)
        return v

    def get_viewer_context(self, user_id: str) -> str:
        """格式化观众上下文 → S2 prompt (合并 L2+L3 能力)"""
        v = self._viewers.get(user_id)
        if not v:
            return ""

        weight = self.retrieval_weight(user_id)
        if weight < 0.1:
            return ""

        loyalty_labels = {0: "新观众", 1: "常客", 2: "老粉", 3: "铁粉"}
        level_label = self.activity_level(user_id)
        parts = [
            f"{v.display_name}({loyalty_labels.get(v.loyalty_level, '观众')}·{level_label})"
        ]

        if v.interaction_count > 3:
            parts.append(f"互动{v.interaction_count}次")
        if v.topics:
            parts.append(f"常聊: {', '.join(v.topics[-5:])}")
        if v.known_facts:
            facts = [f"{k}={val}" for k, val in list(v.known_facts.items())[:5]]
            parts.append(f"已知: {', '.join(facts)}")
        if v.interaction_style:
            parts.append(f"互动偏好: {v.interaction_style}")

        # 关系图谱 (L3)
        related = self.get_related_viewers(user_id)
        if related:
            names = [
                self._viewers[uid].display_name or uid
                for uid, _ in related[:3] if uid in self._viewers
            ]
            if names:
                parts.append(f"常互动观众: {', '.join(names)}")

        # 高光时刻 (L3)
        moments = self._moments.get(user_id, [])
        if moments:
            top = sorted(moments, key=lambda m: m["score"], reverse=True)[:2]
            parts.append(f"记忆: {'; '.join(m['moment'][:20] for m in top)}")

        if v.notes:
            parts.append(v.notes)

        return " | ".join(parts)

    def search_viewers(self, name_hint: str) -> list[ViewerProfile]:
        hl = name_hint.lower()
        results = []
        for v in self._viewers.values():
            if hl in v.display_name.lower() or hl in v.user_id.lower():
                results.append(v)
        return results[:5]

    # ── 关系图谱 (保留 L3) ──────────────────────────────

    def record_coviewer_interaction(self, user_a: str, user_b: str) -> None:
        """记录两个观众在同一场直播互动 → 增加关联强度"""
        for uid in (user_a, user_b):
            if uid not in self._viewers:
                self.upsert_viewer(uid)

        self._relationships.setdefault(user_a, {})
        self._relationships.setdefault(user_b, {})
        self._relationships[user_a][user_b] = (
            self._relationships[user_a].get(user_b, 0) + 0.5
        )
        self._relationships[user_b][user_a] = (
            self._relationships[user_b].get(user_a, 0) + 0.5
        )

    def get_related_viewers(self, user_id: str,
                            min_strength: float = 0.3) -> list[tuple[str, float]]:
        rel = self._relationships.get(user_id, {})
        return [(uid, s) for uid, s in rel.items() if s >= min_strength]

    # ── 高光时刻 (保留 L3) ──────────────────────────────

    def record_moment(self, user_id: str, moment: str, score: float = 5.0) -> None:
        if user_id not in self._viewers:
            self.upsert_viewer(user_id)
        self._moments.setdefault(user_id, [])
        self._moments[user_id].append({
            "moment": moment, "score": score, "timestamp": time.time(),
        })
        if len(self._moments[user_id]) > 20:
            self._moments[user_id] = sorted(
                self._moments[user_id], key=lambda m: m["score"], reverse=True
            )[:15]

    # ── FAQ 缓存 (保留 L2, 升级语义匹配) ──────────────────

    def faq_lookup(self, query: str) -> str | None:
        """FAQ 精确匹配 (语义匹配由 Graphiti search 提供)"""
        key = self._norm(query)
        if not key:
            return None

        if key in self._faq:
            reply, _ = self._faq[key]
            self._faq[key] = (reply, time.time())
            self._faq.move_to_end(key)
            return reply
        return None

    def faq_set(self, query: str, reply: str) -> None:
        key = self._norm(query)
        if not key or not reply:
            return
        if key in self._faq:
            self._faq[key] = (reply, time.time())
            self._faq.move_to_end(key)
            return
        if len(self._faq) >= self._max_faq:
            self._faq.popitem(last=False)
        self._faq[key] = (reply, time.time())

    # ── 记忆自主决策 (Letta 核心能力) ─────────────────────

    def should_remember(self, user_id: str, fact_importance: float = 0.5) -> bool:
        """判断一条事实值不值得存入长期记忆 (Archival)

        规则:
          - 老粉/铁粉的记忆总是存
          - 重要性 >0.7 总是存
          - 活跃观众 + 重要性 >0.3 存
          - 流失观众放弃
        """
        level = self.activity_level(user_id)
        if level == "流失":
            return False
        if fact_importance > 0.7:
            return True

        v = self._viewers.get(user_id)
        if v and v.loyalty_level >= 2:
            return True
        if level == "活跃" and fact_importance > 0.3:
            return True
        return False

    def should_recall(self, user_id: str, context: dict | None = None) -> float:
        """判断当前需要多深度的记忆检索 → 返回 0.0~1.0

        返回值用于控制检索深度:
          1.0 → 全量检索 (Archival + Recall + 关系图谱)
          0.5 → 中等检索 (Recall + 观众档案)
          0.0 → 仅 Core Memory

        策略:
          - 铁粉/老粉 → 深度检索
          - 提到已知话题 → 中等检索
          - 路人/新观众 → 轻量检索
        """
        v = self._viewers.get(user_id)
        if not v:
            return 0.3

        if v.loyalty_level >= 3:
            return 1.0
        if v.loyalty_level >= 2:
            return 0.8
        if v.loyalty_level >= 1:
            return 0.6
        return 0.3

    def should_forget(self, user_id: str) -> bool:
        """判断观众是否已流失, 可从活跃记忆降权"""
        return self.activity_level(user_id) == "流失"

    # ── 获取活跃观众 ────────────────────────────────────

    def get_active_viewers(self, limit: int = 20) -> list[ViewerProfile]:
        weighted = [(v, self.retrieval_weight(uid))
                    for uid, v in self._viewers.items()]
        weighted.sort(key=lambda x: -x[1])
        return [v for v, _ in weighted[:limit]]

    # ── 获取全部观众档案 (GUI 用) ───────────────────────

    def get_all_viewers(self) -> list[dict]:
        viewers = []
        for uid, v in self._viewers.items():
            viewers.append({
                "user_id": v.user_id,
                "display_name": v.display_name,
                "platform": v.platform,
                "interaction_count": v.interaction_count,
                "loyalty_level": v.loyalty_level,
                "first_seen": v.first_seen,
                "last_seen": v.last_seen,
                "topics": v.topics,
                "known_facts": v.known_facts,
                "interaction_style": v.interaction_style,
            })
        viewers.sort(key=lambda x: -x["interaction_count"])
        return viewers

    # ── 统计 ────────────────────────────────────────────

    @property
    def viewer_count(self) -> int:
        return len(self._viewers)

    @property
    def interaction_count(self) -> int:
        return len(self._interactions)

    @property
    def faq_count(self) -> int:
        return len(self._faq)

    # ── 持久化 ──────────────────────────────────────────

    def save(self) -> None:
        """保存观众档案 + FAQ + 关系图谱 + 高光时刻 到 JSON"""
        data = {
            "viewers": {
                uid: {
                    "user_id": v.user_id,
                    "display_name": v.display_name,
                    "platform": v.platform,
                    "first_seen": v.first_seen,
                    "last_seen": v.last_seen,
                    "interaction_count": v.interaction_count,
                    "loyalty_level": v.loyalty_level,
                    "topics": v.topics,
                    "known_facts": v.known_facts,
                    "interaction_style": v.interaction_style,
                    "notes": v.notes,
                }
                for uid, v in self._viewers.items()
            },
            "faq": [[k, v[0]] for k, v in self._faq.items()][-200:],
            "relationships": {
                uid: dict(rels) for uid, rels in self._relationships.items()
            },
            "moments": self._moments,
        }
        try:
            (self._dir / "viewers.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, PermissionError, IOError):
            pass

    def _load(self) -> None:
        path = self._dir / "viewers.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for uid, vd in data.get("viewers", {}).items():
                try:
                    self._viewers[uid] = ViewerProfile(
                        user_id=vd.get("user_id", uid),
                        display_name=vd.get("display_name", ""),
                        platform=vd.get("platform", ""),
                        first_seen=vd.get("first_seen", 0.0),
                        last_seen=vd.get("last_seen", 0.0),
                        interaction_count=vd.get("interaction_count", 0),
                        loyalty_level=vd.get("loyalty_level", 0),
                        topics=vd.get("topics", []),
                        known_facts=vd.get("known_facts", {}),
                        interaction_style=vd.get("interaction_style", ""),
                        notes=vd.get("notes", ""),
                    )
                except Exception:
                    pass
            for item in data.get("faq", []):
                if isinstance(item, list) and len(item) == 2:
                    self._faq[str(item[0])] = (str(item[1]), 0.0)
            self._relationships = data.get("relationships", {})
            self._moments = data.get("moments", {})
        except (json.JSONDecodeError, OSError):
            pass

    # ── 工具 ────────────────────────────────────────────

    @staticmethod
    def _norm(text: str) -> str:
        import re
        return re.sub(r'[^\w一-鿿]', '', text.strip().lower())
