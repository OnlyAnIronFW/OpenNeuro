"""事件总线 — 发布/订阅 + 持久化日志"""

import asyncio
import json
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Callable, Awaitable, Dict, List, Optional

from .types import Event, EventType

Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    """异步事件总线，支持发布/订阅和事件日志持久化"""

    def __init__(self, log_dir: Optional[str] = None):
        self._handlers: Dict[str, List[Handler]] = defaultdict(list)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._running = False
        self._dispatch_tasks: list[asyncio.Task] = []
        self._log_path: Optional[Path] = None
        self._log_handle = None  # 文件句柄, start() 后可用

        if log_dir:
            p = Path(log_dir)
            p.mkdir(parents=True, exist_ok=True)
            self._log_path = p / f"events_{int(time.time())}.jsonl"

    # ── 订阅管理 ───────────────────────────────────────

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """订阅事件类型。用 '*' 订阅所有事件。"""
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        if event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h is not handler
            ]

    # ── 发布 ───────────────────────────────────────────

    async def publish(self, event: Event) -> None:
        """发布事件到队列"""
        await self._queue.put(event)

    async def publish_nowait(self, event: Event) -> None:
        """非阻塞发布 (队列满时丢弃)"""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    # ── 派发 ───────────────────────────────────────────

    async def _dispatch(self, event: Event) -> None:
        """分发事件到匹配的处理器"""
        # 持久化 (追加写入)
        if self._log_handle:
            self._log_handle.write(
                json.dumps(
                    {
                        "event_id": event.event_id,
                        "timestamp": event.timestamp,
                        "type": event.type,
                        "source": event.source,
                        "payload": event.payload,
                        "correlation_id": event.correlation_id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            self._log_handle.flush()

        # 精确匹配 + 通配符
        handlers: List[Handler] = []
        handlers.extend(self._handlers.get(event.type, []))
        handlers.extend(self._handlers.get("*", []))

        if not handlers:
            return

        results = await asyncio.gather(
            *[h(event) for h in handlers], return_exceptions=True
        )
        for r in results:
            if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                # 静默记录, 不阻塞派发
                pass

    # ── 生命周期 ───────────────────────────────────────

    async def start(self) -> None:
        """启动事件循环"""
        self._running = True
        if self._log_path:
            self._log_handle = open(str(self._log_path), "a", encoding="utf-8")

    async def stop(self) -> None:
        """停止事件循环"""
        self._running = False
        for t in self._dispatch_tasks:
            t.cancel()
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None
        # 清空队列
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def run_forever(self) -> None:
        """运行事件循环 (阻塞直到 stop)"""
        await self.start()
        try:
            while self._running:
                event = await self._queue.get()
                task = asyncio.create_task(self._dispatch(event))
                self._dispatch_tasks.append(task)
                # 清理已完成的 task
                self._dispatch_tasks = [t for t in self._dispatch_tasks if not t.done()]
        except asyncio.CancelledError:
            await self.stop()

    # ── 便捷方法 ───────────────────────────────────────

    async def emit(
        self, event_type: str, payload: dict = None, source: str = "ai_streamer"
    ) -> None:
        """便捷发布: 自动创建 Event 并入队"""
        event = self.make_event(event_type=event_type, source=source, payload=payload)
        await self.publish(event)

    # ── 工具 ───────────────────────────────────────────

    @staticmethod
    def make_event(
        event_type: str,
        source: str,
        payload: dict = None,
        correlation_id: str = "",
    ) -> Event:
        return Event(
            event_id=f"evt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}",
            timestamp=time.time(),
            type=event_type,
            source=source,
            payload=payload or {},
            correlation_id=correlation_id,
        )

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()
