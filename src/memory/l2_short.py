"""L2 短期记忆 — ChromaDB 持久化 + 语义检索 + 观众档案"""

import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import Levenshtein

# ViewerProfile 规范定义在 memory_manager.py, 此处统一引用
from .memory_manager import (
    ViewerProfile,
)  # 忠诚度公式: loyalty = min(3, interactions // 10)


@dataclass
class InteractionEntry:
    query: str
    reply: str
    user_id: str
    timestamp: float
    s1_token: str = ""
    score: float = 0.0


class ShortTermMemory:
    """
    L2 短期记忆 (Phase 3 — 文件存储, 后续升级 ChromaDB)

    功能:
      - 观众档案: 记住谁是谁
      - 最近互动: 跨会话保留
      - FAQ 加速: 高频问题快速匹配
      - 语义检索: Levenshtein 模糊搜索
    """

    def __init__(self, data_dir: str = "data/memory"):
        self._dir = Path(data_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

        self._viewers: Dict[str, ViewerProfile] = {}
        self._interactions: List[InteractionEntry] = []
        self._faq: OrderedDict[str, Tuple[str, float]] = (
            OrderedDict()
        )  # key → (reply, last_hit)

        self._max_interactions = 5000
        self._max_faq = 200
        self._faq_similarity = 0.85

        self._load()

    # ── 观众档案 ──────────────────────────────────────

    def get_viewer(self, user_id: str) -> Optional[ViewerProfile]:
        return self._viewers.get(user_id)

    def upsert_viewer(
        self, user_id: str, display_name: str = "", platform: str = "", **kwargs
    ) -> ViewerProfile:
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

        # 忠诚度: 每10次互动升一级
        v.loyalty_level = min(3, v.interaction_count // 10)

        for k, val in kwargs.items():
            if hasattr(v, k):
                setattr(v, k, val)

        return v

    def get_viewer_context(self, user_id: str) -> str:
        """生成观众上下文文本 (注入 S2 prompt)"""
        v = self.get_viewer(user_id)
        if not v:
            return ""

        loyalty_labels = {0: "新观众", 1: "常客", 2: "老粉", 3: "铁粉"}
        parts = [f"{v.display_name}({loyalty_labels.get(v.loyalty_level, '观众')})"]

        if v.interaction_count > 3:
            parts.append(f"互动{v.interaction_count}次")
        if v.topics:
            parts.append(f"常聊: {', '.join(v.topics[-5:])}")
        if v.interaction_style:
            parts.append(f"互动偏好: {v.interaction_style}")
        if v.known_facts:
            facts = [f"{k}={val}" for k, val in list(v.known_facts.items())[:5]]
            parts.append(f"已知: {', '.join(facts)}")
        if v.notes:
            parts.append(v.notes)

        return " | ".join(parts)

    def search_viewers(self, name_hint: str) -> List[ViewerProfile]:
        """按名称模糊搜索观众"""
        results = []
        hl = name_hint.lower()
        for v in self._viewers.values():
            if hl in v.display_name.lower() or hl in v.user_id.lower():
                results.append(v)
        return results[:5]

    # ── 互动记录 ──────────────────────────────────────

    def record_interaction(
        self,
        query: str,
        reply: str,
        user_id: str,
        s1_token: str = "",
        score: float = 0.0,
    ) -> None:
        entry = InteractionEntry(
            query=query,
            reply=reply,
            user_id=user_id,
            timestamp=time.time(),
            s1_token=s1_token,
            score=score,
        )
        self._interactions.append(entry)
        if len(self._interactions) > self._max_interactions:
            self._interactions = self._interactions[-self._max_interactions :]

    def search_interactions(self, query: str, limit: int = 5) -> List[InteractionEntry]:
        """语义搜索相关互动"""
        nq = self._norm(query)
        if not nq:
            return []

        scored = []
        for entry in self._interactions[-1000:]:  # 最多搜索最近1000条
            ne = self._norm(entry.query)
            max_len = max(len(nq), len(ne)) if ne else 1
            dist = Levenshtein.distance(nq, ne) if ne else 999
            sim = 1.0 - (dist / max_len)
            if sim > 0.6:
                scored.append((sim, entry))

        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:limit]]

    def get_recent_context(self, user_id: str = "", limit: int = 10) -> str:
        """最近互动上下文 (注入 prompt)"""
        items = self._interactions[-50:]
        if user_id:
            items = [e for e in items if e.user_id == user_id]

        if not items:
            return ""

        lines = []
        for e in items[-limit:]:
            lines.append(f"Q: {e.query[:40]} → A: {e.reply[:40]}")
        return "最近互动:\n" + "\n".join(lines)

    # ── FAQ 缓存 ──────────────────────────────────────

    def faq_get(self, query: str) -> Optional[str]:
        """查FAQ缓存 (精确+模糊)"""
        nq = self._norm(query)
        if not nq:
            return None

        if nq in self._faq:
            reply, _ = self._faq[nq]
            self._faq[nq] = (reply, time.time())
            self._faq.move_to_end(nq)
            return reply

        for key, (reply, _) in self._faq.items():
            max_len = max(len(nq), len(key))
            dist = Levenshtein.distance(nq, key)
            sim = 1.0 - (dist / max_len) if max_len else 0
            if sim >= self._faq_similarity:
                self._faq[key] = (reply, time.time())
                self._faq.move_to_end(key)
                return reply

        return None

    def faq_set(self, query: str, reply: str) -> None:
        nq = self._norm(query)
        if not nq or not reply:
            return

        if nq in self._faq:
            self._faq[nq] = (reply, time.time())
            self._faq.move_to_end(nq)
            return

        if len(self._faq) >= self._max_faq:
            self._faq.popitem(last=False)

        self._faq[nq] = (reply, time.time())

    # ── 持久化 ────────────────────────────────────────

    def save(self) -> None:
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
        }
        try:
            (self._dir / "viewers.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, PermissionError, IOError):
            pass  # 磁盘满/权限问题, 不崩溃

    def _load(self) -> None:
        path = self._dir / "viewers.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for uid, vd in data.get("viewers", {}).items():
                # 兼容旧格式: 字段缺失时用默认值
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
                    pass  # 单条损坏不影响其他
            for item in data.get("faq", []):
                if isinstance(item, list) and len(item) == 2:
                    self._faq[str(item[0])] = (str(item[1]), 0.0)
        except (json.JSONDecodeError, KeyError, OSError):
            pass  # 文件损坏/不可读, 不崩溃, 从空开始

    # ── 工具 ──────────────────────────────────────────

    @staticmethod
    def _norm(text: str) -> str:
        import re

        return re.sub(r"[^\w一-鿿]", "", text.strip().lower())

    @property
    def viewer_count(self) -> int:
        return len(self._viewers)

    @property
    def interaction_count(self) -> int:
        return len(self._interactions)

    @property
    def faq_count(self) -> int:
        return len(self._faq)
