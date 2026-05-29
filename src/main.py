"""AI 主播主控制器 — 消息→S1→S2→清洗→发送 完整闭环"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from src.config.loader import ConfigManager
from src.prompts.assembler import PromptAssembler
from src.models.s1_client import MiniCPMClient
from src.models.s2_client import DeepSeekClient, S2Response
from src.s1.engine import S1Engine
from src.s1.parser import S1Token
from src.s1.rule_engine import RuleConfig
from src.s2.cleaner import S2OutputCleaner
from src.s2.cache import SemanticCache
from src.memory.l1_working import WorkingMemory
from src.memory.graphiti_store import GraphitiStore
from src.memory.memory_manager import MemoryManager
from src.threads.manager import ThreadManager
from src.emotion.model import EmotionalState
from src.iteration.recorder import Recorder
from src.session.degradation import DegradationManager, DegradationLevel
from src.session.lifecycle import Session
from src.observability.metrics import metrics
from src.observability.security import BehaviorMonitor
from src.events.bus import EventBus
from src.iteration.s1_trainer import S1TrainingCollector
from src.vision.pipeline import VisualPipeline
from src.utils.logger import log_manager

_log = log_manager.get("main")


@dataclass
class ReplyRecord:
    text: str
    trigger_msg: dict
    timestamp: float
    s1_token: str
    s1_confidence: Optional[float] = None
    s2_thinking: str = ""
    s2_latency_ms: float = 0.0
    cache_hit: bool = False
    clean_warnings: List[str] = field(default_factory=list)


class AIStreamer:
    """AI 主播主控制器 — Phase 1 完整闭环"""

    def __init__(self, config_path: str = "config.yaml"):
        self._cfg = ConfigManager(config_path)
        self._cfg.load()
        c = self._cfg.current

        # Prompt 引擎
        self._prompts = PromptAssembler()

        # S1 (MiniCPM)
        s1_client = MiniCPMClient(
            base_url="http://localhost:19060",
            timeout_ms=c.s1_decision.silence_watchdog_ms / 10,
            mock_mode=not self._check_s1(),
        )
        rule_cfg = RuleConfig(
            protection_period_ms=c.s1_decision.protection_period_ms,
            max_replies_per_10s=c.s1_decision.max_replies_per_10s,
            silence_watchdog_ms=c.s1_decision.silence_watchdog_ms,
            forced_cooldown_ms=c.s1_decision.forced_cooldown_ms,
            quick_reply_max_chars=c.s1_decision.quick_reply_max_chars,
        )
        self._s1 = S1Engine(s1_client, self._prompts, rule_cfg)

        # S2 (DeepSeek)
        self._s2 = DeepSeekClient(
            api_key=c.s2_model.api_key,
            api_base=c.s2_model.api_base,
            model=c.s2_model.model,
            temperature=c.s2_model.temperature,
            top_p=c.s2_model.top_p,
            timeout_ms=c.s2_model.timeout_ms,
            mock_mode=not (
                c.s2_model.api_key and not c.s2_model.api_key.startswith("${")
            ),
        )

        # 输出清洗
        self._cleaner = S2OutputCleaner()

        # 语义缓存
        self._cache = SemanticCache(
            max_size=c.memory.l2.semantic_cache.ttl_hours * 20,
            similarity_threshold=c.memory.l2.semantic_cache.similarity_threshold,
            ttl_seconds=c.memory.l2.semantic_cache.ttl_hours * 3600,
        )

        # 记忆系统
        self._wm = WorkingMemory()
        self._graphiti = GraphitiStore("data/graphiti")
        self._memory = MemoryManager(self._graphiti, l1=self._wm)
        self._use_graphiti = bool(
            os.environ.get("DEEPSEEK_API_KEY")
        )  # 有 API key 才启用

        # 线程管理
        self._threads = ThreadManager()

        # 情绪模型
        self._emotion = EmotionalState()

        # 录制器 (可选)
        self._recorder: Optional[Recorder] = None
        if c.session.auto_save_recording:
            self._recorder = Recorder(c.session.recording_path)

        # 降级管理
        self._degradation = DegradationManager()
        # 行为监控
        self._behavior = BehaviorMonitor()
        # 事件总线
        self._event_bus = EventBus(log_dir="data/events")

        # S1 训练数据收集器 (Phase 6)
        self._trainer = S1TrainingCollector()

        # 情绪触发辅助状态
        self._last_msg_time: float = time.time()
        self._recent_msg_times: List[float] = []

        # 工作状态
        self._running = False
        self._reply_history: List[ReplyRecord] = []
        self._language = "中文"
        self._session = Session()

    # ── 生命周期 ──────────────────────────────────────

    async def start(self) -> None:
        await _log.start()
        await self._s1.start()
        await self._s2.start()
        if self._use_graphiti:
            try:
                await self._graphiti.start()
            except Exception as e:
                print(f"[Main] Graphiti 启动失败, 降级到 L2: {e}")
                try:
                    await self._graphiti.close()
                except Exception:
                    pass
                self._use_graphiti = False
        # 事件总线 (启动后台派发器)
        await self._event_bus.start()
        self._event_bus_task = asyncio.create_task(self._event_bus.run_forever())
        self._running = True
        self._session.start()
        if self._recorder:
            self._recorder.start()
        _log.info(
            "streamer_started",
            s1_mode="mock" if self._s1._client._mock_mode else "real",
            s2_mode="mock" if self._s2._mock_mode else "real",
            memory_mode="graphiti" if self._use_graphiti else "l2",
        )

    async def stop(self) -> None:
        self._running = False
        await self._s1.stop()
        await self._s2.stop()
        self._threads.prune_stale()
        self._memory.save()
        if self._graphiti is not None:
            await self._graphiti.close()
        if self._recorder:
            self._recorder.stop()
        self._session.end()
        self._session.save()
        _log.info(
            "streamer_stopped",
            reply_count=len(self._reply_history),
            viewer_count=self._memory.viewer_count,
            cache_hit_rate=self._cache.stats.hit_rate,
        )
        if hasattr(self, "_event_bus_task"):
            self._event_bus_task.cancel()
        await self._event_bus.stop()
        await _log.stop()

    # ── 核心: 消息处理 ───────────────────────────────

    async def handle_message(self, msg: Dict[str, Any]) -> Optional[str]:
        """
        处理一条消息的完整闭环。

        Returns:
            发送的回复文本, 或 None (本次不回复)
        """
        if not self._running:
            return None

        t_start = time.perf_counter()
        user_id = msg.get("user_id", msg.get("user", "anonymous"))

        await self._event_bus.emit(
            "PLATFORM_MESSAGE_RECEIVED",
            {
                "user": msg.get("user"),
                "text": msg.get("text"),
                "event_type": msg.get("event_type"),
            },
        )

        # ── 0. 记忆 + 线程 + 录制: 记录消息 ──
        self._wm.add_message(msg)
        thread_id = self._threads.on_message(msg)
        await self._event_bus.emit("THREAD_CREATED", {"thread_id": thread_id})
        if self._recorder:
            self._recorder.record_message(
                msg.get("user", "?"),
                msg.get("text", ""),
                msg.get("mentioned_bot", False),
                msg.get("is_question", False),
            )

        # ── 1. S1 决策 ──
        metrics.inc("s1.decisions.total")
        s1_result = await self._s1.decide(
            messages=[msg],
            thread_snapshot=self._threads.snapshot(),
            working_memory={
                "seconds_since_last_reply": self._s1.seconds_since_last_reply
            },
        )

        # ── 1b. 记录 S1 训练数据 ──
        self._trainer.record(
            messages=[msg],
            s1_raw=s1_result.raw_s1_output,
            s1_token=s1_result.parsed.token.value,
            s1_confidence=s1_result.parsed.confidence or 0,
            thread_snapshot=self._threads.snapshot(),
        )

        # ── 2. 不回复 → 返回 ──
        if not s1_result.parsed.is_reply:
            _log.debug(
                "s1_no_reply",
                token=s1_result.parsed.token.value,
                user=msg.get("user", "?"),
                text=msg.get("text", "")[:30],
            )
            if self._recorder:
                self._recorder.record_s1_decision(
                    s1_result.parsed.token.value,
                    s1_result.parsed.confidence or 0,
                    s1_result.parsed.direction or "",
                    s1_result.s1_latency_ms,
                )
            return None

        token = s1_result.parsed.token
        direction = (
            s1_result.parsed.direction or s1_result.parsed.quick_reply_text or ""
        )
        confidence = s1_result.parsed.confidence or 0.5

        await self._event_bus.emit(
            "S1_DECISION_MADE",
            {
                "token": s1_result.parsed.token.value,
                "confidence": s1_result.parsed.confidence or 0,
            },
        )

        # ── 3. Quick-Reply → 直接输出 ──
        if token == S1Token.QUICK_REPLY and s1_result.parsed.quick_reply_text:
            reply = s1_result.parsed.quick_reply_text
            self._s1.record_reply()
            if self._recorder:
                self._recorder.record_s1_decision(
                    token.value, confidence, reply, s1_result.s1_latency_ms
                )
                self._recorder.record_s2_reply(reply, 0, "non-think", 0)
            self._update_emotion(msg)
            self._record(
                ReplyRecord(
                    text=reply,
                    trigger_msg=msg,
                    timestamp=time.time(),
                    s1_token="Quick-Reply",
                    s1_confidence=confidence,
                    s2_latency_ms=0,
                    cache_hit=False,
                )
            )
            # Quick-Reply 也写入记忆
            self._memory.record_recall(
                query=msg.get("text", ""),
                reply=reply,
                user_id=user_id,
                s1_token="Quick-Reply",
            )
            self._memory.upsert_viewer(user_id, msg.get("user", ""))
            # 后台异步写入 Graphiti
            if self._use_graphiti:
                asyncio.create_task(
                    self._memory.store_archival(
                        user_id,
                        msg.get("user", ""),
                        msg.get("text", ""),
                        reply,
                    )
                )
            return reply

        # ── 4. Start-Speaking → S2 生成 ──

        # 4a. 查缓存
        cache_key = f"{direction}|{msg.get('text', '')}"
        cached = self._cache.get(cache_key)
        if cached:
            self._s1.record_reply()
            self._record(
                ReplyRecord(
                    text=cached,
                    trigger_msg=msg,
                    timestamp=time.time(),
                    s1_token="Start-Speaking",
                    s1_confidence=confidence,
                    s2_latency_ms=0,
                    cache_hit=True,
                )
            )
            return cached

        # 4b. 记忆检索: 观众档案 + 最近互动 (Graphiti / L2 fallback)
        viewer_ctx = self._memory.get_viewer_context(user_id)
        recent_ctx = self._memory.get_recent_context(user_id, limit=5)
        if not viewer_ctx and msg.get("user"):
            # 首次出现 → 自动建档
            self._memory.upsert_viewer(user_id, msg.get("user", ""))

        # 4c. 构建 S2 prompt (注入记忆)
        system = self._prompts.build_s2_system()
        first_msg = self._prompts.build_s2_first_user_message(self._language)
        user_msg = self._prompts.build_s2_user_message(
            reply_direction=direction,
            triggering_messages=f"[{msg.get('user', '?')}] {msg.get('text', '')}",
            retrieved_memories=recent_ctx,
            viewer_profile=viewer_ctx,
            s1_confidence=confidence,
            emotional_state=self._emotion.to_prompt_str(),
            seconds_since_last_reply=self._s1.seconds_since_last_reply,
        )

        # 4c. 调 S2
        s2_resp = await self._s2.generate(
            system_prompt=system,
            user_message=user_msg,
            first_user_message=first_msg,
            s1_confidence=confidence,
            max_tokens=512,
        )

        await self._event_bus.emit(
            "S2_RESPONSE_RECEIVED",
            {
                "latency_ms": s2_resp.total_ms,
                "cache_hit": False,
            },
        )

        if s2_resp.error:
            metrics.inc("s2.errors.total")
            self._degradation.record_s2_error(s2_resp.error)
            await self._event_bus.emit(
                "SYSTEM_DEGRADATION_CHANGED",
                {
                    "level": str(self._degradation.level),
                },
            )
            # 降级: 用 S1 的方向作为回复
            fallback = direction[:80] if direction else ""
            if fallback:
                self._s1.record_reply()
                self._record(
                    ReplyRecord(
                        text=fallback,
                        trigger_msg=msg,
                        timestamp=time.time(),
                        s1_token="Start-Speaking",
                        s1_confidence=confidence,
                        s2_latency_ms=s2_resp.total_ms,
                        cache_hit=False,
                    )
                )
                return fallback
            return None

        # 4d. 清洗 (空回复 → 用 S1 方向兜底)
        if not s2_resp.content.strip():
            print(
                f"[S2 Empty] thinking_mode={s2_resp.thinking_mode.value}, "
                f"thinking_len={len(s2_resp.thinking)} — 使用S1方向兜底"
            )
            fallback = direction[:80] if direction else ""
            if fallback:
                self._s1.record_reply()
                self._record(
                    ReplyRecord(
                        text=fallback,
                        trigger_msg=msg,
                        timestamp=time.time(),
                        s1_token="Start-Speaking",
                        s1_confidence=confidence,
                        s2_latency_ms=s2_resp.total_ms,
                        cache_hit=False,
                    )
                )
                return fallback
            return None

        clean_result = self._cleaner.clean(s2_resp.content)

        if clean_result.is_empty:
            return None

        # 4e. 成功指标 + 降级恢复
        metrics.inc("s2.calls.total")
        metrics.record("s2.latency_ms", s2_resp.total_ms)
        self._degradation.record_s2_success()
        # 行为监控
        self._behavior.observe(clean_result.text)

        # 4f. 写入缓存
        self._cache.set(cache_key, clean_result.text)

        # 4f. 情绪更新
        self._update_emotion(msg)

        # 4g. 录制 S2
        if self._recorder:
            self._recorder.record_s2_reply(
                clean_result.text,
                s2_resp.total_ms,
                s2_resp.thinking_mode.value,
                len(s2_resp.thinking),
            )

        # 4h. 记录
        self._s1.record_reply()
        self._record(
            ReplyRecord(
                text=clean_result.text,
                trigger_msg=msg,
                timestamp=time.time(),
                s1_token="Start-Speaking",
                s1_confidence=confidence,
                s2_thinking=s2_resp.thinking,
                s2_latency_ms=s2_resp.total_ms,
                cache_hit=False,
                clean_warnings=clean_result.warnings,
            )
        )

        elapsed = (time.perf_counter() - t_start) * 1000
        _log.info(
            "reply_sent",
            user=msg.get("user", "?"),
            preview=clean_result.text[:30],
            s2_latency=s2_resp.total_ms,
            total_ms=elapsed,
            thinking=s2_resp.thinking_mode.value,
            warnings=clean_result.warnings,
        )
        print(f"[Reply] {clean_result.text[:50]}... ({elapsed:.0f}ms)")

        # 4g. 写入记忆 + 线程标记
        self._memory.record_recall(
            query=msg.get("text", ""),
            reply=clean_result.text,
            user_id=user_id,
            s1_token=s1_result.parsed.token.value,
        )
        self._memory.upsert_viewer(user_id, msg.get("user", ""))
        # 后台异步写入 Graphiti (LLM 事实提取, 不阻塞)
        if self._use_graphiti:
            asyncio.create_task(
                self._memory.store_archival(
                    user_id,
                    msg.get("user", ""),
                    msg.get("text", ""),
                    clean_result.text,
                )
            )
        self._threads.mark_replied(thread_id)

        await self._event_bus.emit(
            "REPLY_SENT",
            {
                "text": clean_result.text[:50],
                "s1_token": s1_result.parsed.token.value,
            },
        )

        return clean_result.text

    async def handle_messages(self, messages: List[Dict[str, Any]]) -> List[str]:
        """批量处理消息"""
        replies = []
        for msg in messages:
            r = await self.handle_message(msg)
            if r:
                replies.append(r)
        return replies

    # ── 查询 ──────────────────────────────────────────

    @property
    def reply_count(self) -> int:
        return len(self._reply_history)

    @property
    def cache_stats(self):
        return self._cache.stats

    def set_language(self, lang: str) -> None:
        self._language = lang

    def set_s2_mock(self, responses: list) -> None:
        self._s2._mock_responses = list(responses)
        self._s2._mock_index = 0

    # ── 内部 ──────────────────────────────────────────

    # ── 情绪关键词 ──────────────────────────────────────────────────
    _COMPLIMENT_WORDS = ("谢谢", "好厉害", "喜欢", "可爱", "棒", "赞")
    _INSULT_WORDS = ("菜", "lj", "下饭", "辣鸡")
    _BAD_WORDS = ("滚", "傻逼", "垃圾", "废物")
    _WIN_WORDS = ("赢了", "通关", "胜利")
    _LOSE_WORDS = ("输了", "失败")
    _RARE_WORDS = ("出货", "稀有", "金色", "ssr")
    _DEATH_WORDS = ("死了", "挂了", "没了")
    _ACHIEVE_WORDS = ("成就", "解锁", "完成")
    _BOSS_WORDS = ("boss", "首领", "关底")

    def _update_emotion(self, msg: Dict[str, Any]) -> None:
        """根据消息更新情绪 — 20路触发全覆盖"""
        now = time.time()
        text = (msg.get("text") or "").lower()
        etype = msg.get("event_type", "")
        user_id = msg.get("user_id", msg.get("user", "anonymous"))

        # ── 1. 时间维度触发 ──────────────────────────────────────
        silence_sec = now - self._last_msg_time
        if silence_sec >= 300:
            self._emotion.trigger("silence_5min")
        elif silence_sec >= 60:
            self._emotion.trigger("silence_1min")

        # 聊天速度 (10秒滑动窗口)
        self._recent_msg_times.append(now)
        cutoff = now - 10
        self._recent_msg_times = [t for t in self._recent_msg_times if t >= cutoff]
        if len(self._recent_msg_times) > 5:
            self._emotion.trigger("high_chat_velocity")

        # ── 2. 事件类型触发 ──────────────────────────────────────
        if etype == "gift":
            self._emotion.trigger(
                "big_gift" if float(msg.get("price", 0)) > 50 else "gift"
            )
        elif etype == "subscription":
            self._emotion.trigger("subscription")
        elif etype == "cutscene":
            self._emotion.trigger("cutscene")

        # ── 3. 消息内容触发 ──────────────────────────────────────
        # 3a. 正向
        if any(w in text for w in self._COMPLIMENT_WORDS):
            self._emotion.trigger("compliment")
        if msg.get("is_question"):
            self._emotion.trigger("interaction_good")

        # 3b. 负向 (insult + interaction_bad 可重叠触发)
        if any(w in text for w in self._INSULT_WORDS):
            self._emotion.trigger("insult")
        if any(w in text for w in self._BAD_WORDS):
            self._emotion.trigger("interaction_bad")

        # 3c. 游戏关键词 (视觉检测 fallback)
        if any(w in text for w in self._WIN_WORDS):
            self._emotion.trigger("game_win")
        if any(w in text for w in self._LOSE_WORDS):
            self._emotion.trigger("game_lose")
        if any(w in text for w in self._RARE_WORDS):
            self._emotion.trigger("rare_drop")
        if any(w in text for w in self._DEATH_WORDS):
            self._emotion.trigger("death")
        if any(w in text for w in self._ACHIEVE_WORDS):
            self._emotion.trigger("achievement")
        if any(w in text for w in self._BOSS_WORDS):
            self._emotion.trigger("boss_encounter")

        # ── 4. 观众属性触发 ──────────────────────────────────────
        if user_id and user_id != "anonymous":
            viewer = self._memory.get_viewer(user_id)
            if viewer is not None:
                if viewer.loyalty_level > 2:
                    self._emotion.trigger("vet_return")
                if viewer.interaction_count == 0:
                    self._emotion.trigger("new_viewer")
            else:
                # 从未建档的全新观众
                self._emotion.trigger("new_viewer")

        # ── 5. 自然衰减 ─────────────────────────────────────────
        self._emotion.decay()
        self._last_msg_time = now

    def _record(self, record: ReplyRecord) -> None:
        self._reply_history.append(record)
        if len(self._reply_history) > 1000:
            self._reply_history = self._reply_history[-500:]

    @staticmethod
    def _check_s1() -> bool:
        """检测 MiniCPM 是否可达"""
        try:
            import urllib.request

            urllib.request.urlopen("http://localhost:9060/health", timeout=1)
            return True
        except Exception:
            return False
