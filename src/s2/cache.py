"""语义缓存 — 相似回复复用 + LRU淘汰 + TTL过期"""

import time
import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional, Tuple, List

import Levenshtein


@dataclass
class CacheEntry:
    query_key: str          # 归一化后的查询键
    reply_text: str
    created_at: float
    last_hit_at: float
    hit_count: int = 0

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def total(self) -> int:
        return self.hits + self.misses


class SemanticCache:
    """
    轻量语义缓存 (Phase 1 — 纯内存, Phase 3 升级为 ChromaDB 向量检索)

    策略:
      1. 归一化查询文本 (去空格/标点/小写)
      2. 精确键匹配 → 命中 (最快)
      3. 归一化后 Levenshtein 相似度 → >阈值命中
      4. LRU 淘汰 + TTL 过期
    """

    def __init__(
        self,
        max_size: int = 500,
        similarity_threshold: float = 0.85,
        ttl_seconds: int = 86400,
    ):
        self._max_size = max_size
        self._similarity_threshold = similarity_threshold
        self._ttl_seconds = ttl_seconds
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._stats = CacheStats()

    # ── 公共接口 ──────────────────────────────────────

    def get(self, query: str) -> Optional[str]:
        """检索缓存回复, 未命中返回 None"""
        query = query.strip()
        if not query:
            return None

        now = time.time()
        key = self._normalize(query)

        # 1. 精确匹配
        if key in self._store:
            entry = self._store[key]
            if self._is_expired(entry, now):
                self._evict(key, "ttl_expired")
                self._stats.expirations += 1
                self._stats.misses += 1
                return None
            entry.last_hit_at = now
            entry.hit_count += 1
            self._store.move_to_end(key)
            self._stats.hits += 1
            return entry.reply_text

        # 2. Levenshtein 模糊匹配
        best_entry, best_sim = self._fuzzy_search(key)
        if best_entry and best_sim >= self._similarity_threshold:
            if not self._is_expired(best_entry, now):
                best_entry.last_hit_at = now
                best_entry.hit_count += 1
                self._store.move_to_end(best_entry.query_key)
                self._stats.hits += 1
                return best_entry.reply_text

        self._stats.misses += 1
        return None

    def set(self, query: str, reply: str) -> None:
        """存入缓存"""
        query = query.strip()
        if not query or not reply.strip():
            return

        key = self._normalize(query)

        # 已存在 → 更新
        if key in self._store:
            self._store[key].reply_text = reply
            self._store[key].created_at = time.time()
            self._store.move_to_end(key)
            return

        # 满 → LRU 淘汰
        if len(self._store) >= self._max_size:
            oldest_key, _ = self._store.popitem(last=False)
            self._stats.evictions += 1

        self._store[key] = CacheEntry(
            query_key=key,
            reply_text=reply,
            created_at=time.time(),
            last_hit_at=time.time(),
        )

    @property
    def stats(self) -> CacheStats:
        return self._stats

    @property
    def size(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()
        self._stats = CacheStats()

    # ── 过期清理 ──────────────────────────────────────

    def prune_expired(self) -> int:
        """清理过期条目, 返回清理数量"""
        now = time.time()
        expired = [
            k for k, e in self._store.items()
            if now - e.created_at > self._ttl_seconds
        ]
        for k in expired:
            self._evict(k, "ttl_expired")
        self._stats.expirations += len(expired)
        return len(expired)

    # ── 内部 ─────────────────────────────────────────

    @staticmethod
    def _normalize(text: str) -> str:
        """归一化: 去空格/标点/小写 → 语义键"""
        import re
        # 移除所有非字母数字的字符 (保留中英文字母数字)
        text = re.sub(r'[^\w一-鿿]', '', text, flags=re.UNICODE)
        return text.lower().strip()

    def _fuzzy_search(self, key: str) -> Tuple[Optional[CacheEntry], float]:
        """Levenshtein 模糊搜索最佳匹配"""
        best_entry = None
        best_sim = 0.0
        for entry in self._store.values():
            max_len = max(len(key), len(entry.query_key))
            if max_len == 0:
                continue
            dist = Levenshtein.distance(key, entry.query_key)
            sim = 1.0 - (dist / max_len)
            if sim > best_sim:
                best_sim = sim
                best_entry = entry
        return best_entry, best_sim

    def _evict(self, key: str, reason: str) -> None:
        self._store.pop(key, None)

    def _is_expired(self, entry: CacheEntry, now: float) -> bool:
        """TTL <= 0 视为立即可过期, 避免浮点精度问题"""
        if self._ttl_seconds <= 0:
            return True
        return now - entry.created_at > self._ttl_seconds
