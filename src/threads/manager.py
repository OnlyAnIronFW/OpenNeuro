"""并发线程管理器 — 话题分簇 + 优先级排序 + 反饥饿"""

import json
import os
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Tuple

import Levenshtein


@dataclass
class ConversationThread:
    thread_id: str
    participants: List[str] = field(default_factory=list)
    topic_label: str = ""
    topic_keywords: List[str] = field(default_factory=list)
    state: str = "active"  # active | waiting_reply | cooling_down | closed
    priority: float = 5.0
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    bot_last_reply_at: float = 0.0
    bot_reply_count: int = 0
    message_count: int = 0
    unread_messages: List[Dict] = field(default_factory=list)

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_activity

    @property
    def seconds_since_bot_reply(self) -> float:
        if not self.bot_last_reply_at:
            return 999.0
        return time.time() - self.bot_last_reply_at


class ThreadManager:
    """并发对话线程管理器"""

    MAX_ACTIVE = 10
    MERGE_SIMILARITY = 0.65
    COOLDOWN_AFTER_REPLIES = 3
    COOLDOWN_MS = 10000
    STALE_MS = 300000
    CLOSE_MS = 900000
    ANTI_STARVATION_BOOST = 2.0

    def __init__(self, persist_dir: Optional[str] = None):
        self._threads: OrderedDict[str, ConversationThread] = OrderedDict()
        self._counter = 0
        self._user_threads: Dict[str, set] = {}  # user_id → {thread_ids}
        self._persist_dir = persist_dir
        if persist_dir:
            self._load_threads()

    # ── 核心: 消息 → 线程 ─────────────────────────────

    def on_message(self, msg: Dict[str, Any]) -> str:
        """将消息分配到线程, 返回 thread_id"""
        user_id = msg.get("user_id", msg.get("user", "anonymous"))
        text = msg.get("text") or ""
        reply_to = msg.get("reply_to_msg_id", "")

        # 1. 回复已知消息 → 同一线程
        if reply_to:
            for t in self._threads.values():
                for um in t.unread_messages:
                    if um.get("message_id") == reply_to:
                        return self._add_to_thread(t.thread_id, msg, user_id)

        # 2. @提及线程参与者
        mentioned_users = self._extract_mentions(text)
        if mentioned_users:
            for t in self._threads.values():
                if any(m in t.participants for m in mentioned_users):
                    return self._add_to_thread(t.thread_id, msg, user_id)

        # 3. 语义相似度 (关键词集合交集 + 模糊匹配)
        best_tid, best_score = None, 0.0
        new_kw = self._extract_keywords(text) if text else []
        new_kw_norm = [self._norm(k) for k in new_kw]
        if new_kw_norm:
            for t in self._threads.values():
                if t.state in ("closed",):
                    continue
                thread_kw_norm = [self._norm(k) for k in t.topic_keywords]
                # 精确关键词交集
                exact_hits = len(set(new_kw_norm) & set(thread_kw_norm))
                if exact_hits > 0:
                    score = exact_hits * 0.8 + 0.2  # 高置信
                else:
                    # 模糊匹配
                    best_pair_sim = 0.0
                    for nk in new_kw_norm:
                        for tk in thread_kw_norm:
                            if not nk or not tk:
                                continue
                            dist = Levenshtein.distance(nk, tk)
                            max_len = max(len(nk), len(tk))
                            sim = 1.0 - (dist / max_len) if max_len else 0
                            if sim > best_pair_sim:
                                best_pair_sim = sim
                    score = best_pair_sim
                if score > best_score:
                    best_score = score
                    best_tid = t.thread_id

        if best_score >= self.MERGE_SIMILARITY and best_tid:
            return self._add_to_thread(best_tid, msg, user_id)

        # 4. 共同参与者 (同一个人的新消息)
        if user_id and user_id in self._user_threads:
            active_tids = [
                tid
                for tid in self._user_threads[user_id]
                if tid in self._threads and self._threads[tid].state == "active"
            ]
            if active_tids:
                return self._add_to_thread(active_tids[0], msg, user_id)

        # 5. 新建线程
        return self._create_thread(msg, user_id, text)

    # ── 优先级队列 ────────────────────────────────────

    def next_to_reply(self) -> Optional[ConversationThread]:
        """返回优先级最高的待回复线程"""
        candidates = []
        for t in self._threads.values():
            if t.state in ("active", "waiting_reply") and t.unread_messages:
                t.priority = self._calc_priority(t)
                candidates.append(t)

        if not candidates:
            return None

        candidates.sort(key=lambda t: -t.priority)
        return candidates[0]

    def mark_replied(self, thread_id: str) -> None:
        t = self._threads.get(thread_id)
        if not t:
            return
        t.bot_last_reply_at = time.time()
        t.bot_reply_count += 1
        t.unread_messages.clear()

        if t.bot_reply_count >= self.COOLDOWN_AFTER_REPLIES:
            t.state = "cooling_down"
            t.priority -= 5.0
            # 延迟恢复
            import asyncio

            try:
                loop = asyncio.get_event_loop()
                loop.call_later(
                    self.COOLDOWN_MS / 1000, lambda: self._restore_if_cooling(thread_id)
                )
            except RuntimeError:
                pass

    def mark_message_handled(self, thread_id: str, msg_id: str) -> None:
        t = self._threads.get(thread_id)
        if t:
            t.unread_messages = [
                m for m in t.unread_messages if m.get("message_id") != msg_id
            ]

    # ── 线程管理 ──────────────────────────────────────

    def snapshot(self) -> List[Dict]:
        """线程快照 (供 S1 决策)"""
        return [
            {
                "id": t.thread_id,
                "participants": t.participants,
                "topic_label": t.topic_label,
                "priority": t.priority,
                "state": t.state,
                "message_count": t.message_count,
                "unread": len(t.unread_messages),
            }
            for t in self._threads.values()
            if t.state != "closed"
        ]

    def prune_stale(self) -> int:
        """清理过期线程, 返回清理数"""
        now = time.time()
        removed = 0
        for tid in list(self._threads.keys()):
            t = self._threads[tid]
            idle_ms = (now - t.last_activity) * 1000
            if idle_ms > self.CLOSE_MS:
                t.state = "closed"
                removed += 1
            elif idle_ms > self.STALE_MS and t.state == "active":
                t.state = "cooling_down"
        # 清理 closed 超过1小时的
        for tid in list(self._threads.keys()):
            t = self._threads[tid]
            if t.state == "closed" and (now - t.last_activity) > 3600:
                del self._threads[tid]
        self._save_threads()
        return removed

    def merge_threads(self, tid_a: str, tid_b: str) -> Optional[str]:
        """合并两个线程, 返回保留的 thread_id"""
        ta = self._threads.get(tid_a)
        tb = self._threads.get(tid_b)
        if not ta or not tb:
            return None
        ta.participants = list(set(ta.participants + tb.participants))
        ta.unread_messages.extend(tb.unread_messages)
        ta.message_count += tb.message_count
        ta.topic_keywords = list(set(ta.topic_keywords + tb.topic_keywords))
        self._threads.pop(tid_b, None)
        return tid_a

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._threads.values() if t.state == "active")

    @property
    def total_count(self) -> int:
        return len(self._threads)

    def reset(self) -> None:
        self._threads.clear()
        self._user_threads.clear()
        self._counter = 0

    # ── 持久化 ────────────────────────────────────────

    def _save_threads(self) -> None:
        """保存活跃线程到 persist_dir/threads.json"""
        if not self._persist_dir:
            return
        os.makedirs(self._persist_dir, exist_ok=True)
        data = {
            "threads": [
                {
                    "thread_id": t.thread_id,
                    "participants": t.participants,
                    "topic_label": t.topic_label,
                    "topic_keywords": t.topic_keywords,
                    "state": t.state,
                    "priority": t.priority,
                    "created_at": t.created_at,
                    "last_activity": t.last_activity,
                    "bot_last_reply_at": t.bot_last_reply_at,
                    "bot_reply_count": t.bot_reply_count,
                    "message_count": t.message_count,
                    "unread_messages": t.unread_messages,
                }
                for t in self._threads.values()
            ],
            "user_threads": {
                uid: list(tids) for uid, tids in self._user_threads.items()
            },
            "counter": self._counter,
        }
        path = os.path.join(self._persist_dir, "threads.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_threads(self) -> None:
        """从 persist_dir/threads.json 恢复线程"""
        if not self._persist_dir:
            return
        path = os.path.join(self._persist_dir, "threads.json")
        if not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._counter = data.get("counter", 0)
        for td in data.get("threads", []):
            t = ConversationThread(
                thread_id=td["thread_id"],
                participants=td.get("participants", []),
                topic_label=td.get("topic_label", ""),
                topic_keywords=td.get("topic_keywords", []),
                state=td.get("state", "active"),
                priority=td.get("priority", 5.0),
                created_at=td.get("created_at", time.time()),
                last_activity=td.get("last_activity", time.time()),
                bot_last_reply_at=td.get("bot_last_reply_at", 0.0),
                bot_reply_count=td.get("bot_reply_count", 0),
                message_count=td.get("message_count", 0),
                unread_messages=td.get("unread_messages", []),
            )
            self._threads[t.thread_id] = t
        for uid, tids in data.get("user_threads", {}).items():
            self._user_threads[uid] = set(tids)

    # ── 内部 ──────────────────────────────────────────

    def _add_to_thread(self, tid: str, msg: Dict, user_id: str) -> str:
        t = self._threads.get(tid)
        if not t:
            return self._create_thread(msg, user_id, msg.get("text", ""))

        display_name = msg.get("user", "")
        if user_id and user_id not in t.participants:
            t.participants.append(user_id)
        # 也存 display_name 用于 @提及匹配
        if display_name and display_name not in t.participants:
            t.participants.append(display_name)
        t.unread_messages.append(self._normalize_msg(msg))
        t.message_count += 1
        t.last_activity = time.time()
        # cooling_down 是发言惩罚, 新消息不解除 (等定时器恢复)

        if user_id:
            self._user_threads.setdefault(user_id, set()).add(tid)

        # 更新话题关键字
        text = msg.get("text", "")
        if text:
            keywords = self._extract_keywords(text)
            # 合并关键词: 新词优先, 去重保序, 最多10个
            merged = keywords.copy()
            for kw in t.topic_keywords:
                if kw not in merged:
                    merged.append(kw)
            t.topic_keywords = merged[:10]
            if not t.topic_label:
                t.topic_label = text[:30]

        return tid

    def _create_thread(self, msg: Dict, user_id: str, text: str) -> str:
        # 上限 → 关闭最低优先级的
        active = [
            t for t in self._threads.values() if t.state in ("active", "waiting_reply")
        ]
        if len(active) >= self.MAX_ACTIVE:
            lowest = min(active, key=lambda t: t.priority)
            lowest.state = "cooling_down"

        tid = f"thr_{uuid.uuid4().hex[:8]}"
        t = ConversationThread(
            thread_id=tid,
            topic_label=text[:30] if text else "",
            topic_keywords=self._extract_keywords(text) if text else [],
        )
        if user_id:
            t.participants.append(user_id)
            self._user_threads.setdefault(user_id, set()).add(tid)
        display_name = msg.get("user", "")
        if display_name and display_name not in t.participants:
            t.participants.append(display_name)
        t.unread_messages.append(self._normalize_msg(msg))
        t.message_count = 1
        self._threads[tid] = t
        return tid

    def _calc_priority(self, t: ConversationThread) -> float:
        score = 5.0

        # 基础权重: 检查未读消息
        for um in t.unread_messages:
            if um.get("mentioned_bot"):
                score += 3.0
            if um.get("is_question"):
                score += 2.0

        # 参与者权重 (仅计算 user_id, 跳过 display_name)
        max_loyalty = 0
        for p in t.participants:
            if p not in self._user_threads:
                continue  # 是 display_name, 跳过
            appearances = len(self._user_threads[p] & set(self._threads.keys()))
            max_loyalty = max(max_loyalty, min(3, appearances))
        score += max_loyalty

        # 紧急度
        wait = t.seconds_since_bot_reply
        if t.bot_reply_count == 0 and wait > 10:
            score += min(wait / 5, 5.0)  # 从未回复, 等待越久越急
        elif t.unread_messages and wait > 30:
            score += 2.0

        # 冷却惩罚
        if t.state == "cooling_down":
            score -= 5.0

        # 反饥饿: 从未回复过的线程加分
        if t.bot_reply_count == 0:
            score += self.ANTI_STARVATION_BOOST

        return score

    def _restore_if_cooling(self, tid: str) -> None:
        t = self._threads.get(tid)
        if t and t.state == "cooling_down":
            t.state = "active"
            t.bot_reply_count = 0  # 重置计数

    # ── 工具 ──────────────────────────────────────────

    @staticmethod
    def _extract_mentions(text: str) -> List[str]:
        """提取 @用户名 (中英文兼容)"""
        if not text:
            return []
        mentions = re.findall(r"@([一-鿿]{1,4})", text)
        mentions.extend(re.findall(r"@([a-zA-Z0-9_]{3,20})", text))
        return mentions

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """提取关键词 (中文滑动窗口 + 英文单词)"""
        if not text:
            return []
        words = []
        # 英文单词
        words.extend(re.findall(r"[a-zA-Z]{2,}", text))
        # 中文滑动窗口
        segs = re.findall(r"[一-鿿]{2,}", text)
        for seg in segs:
            seg_len = len(seg)
            for wlen in (4, 3, 2):
                for i in range(seg_len - wlen + 1):
                    words.append(seg[i : i + wlen])
        # 去重
        seen = set()
        result = []
        for w in words:
            if w not in seen:
                seen.add(w)
                result.append(w)
        return result[:15]

    @staticmethod
    def _normalize_msg(msg: Dict) -> Dict:
        return {
            "user": msg.get("user", "?"),
            "user_id": msg.get("user_id", ""),
            "text": msg.get("text", ""),
            "message_id": msg.get("message_id", ""),
            "timestamp": msg.get("timestamp", time.time()),
            "mentioned_bot": msg.get("mentioned_bot", False),
            "is_question": msg.get("is_question", False),
        }

    @staticmethod
    def _norm(text: str) -> str:
        import re

        return re.sub(r"[^\w一-鿿]", "", text.strip().lower())
