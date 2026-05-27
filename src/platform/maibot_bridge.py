"""MaiBot Live Hub 桥接 — 从 MaiBot WebSocket 接收弹幕, 喂给 AI Streamer"""

import asyncio
import json
import time
from typing import Optional

import aiohttp

from .base import PlatformAdapter, UnifiedMessage


class MaiBotBridge(PlatformAdapter):
    """连接到 MaiBot Live Hub 的 WebSocket, 接收弹幕事件"""

    def __init__(self, hub_url: str = "http://127.0.0.1:8080"):
        super().__init__("maibot")
        self._hub_url = hub_url.rstrip("/")
        self._ws = None
        self._running = False
        self._stats = {"messages": 0, "gifts": 0, "blocked": 0}
        # 屏蔽名单: 这些用户的消息不进入消息流
        self._blocked_users = {"Neuro-sama", "Neuro", "新露", "NewRoad", "AI主播"}

    def block_user(self, username: str) -> None:
        self._blocked_users.add(username)

    def unblock_user(self, username: str) -> None:
        self._blocked_users.discard(username)

    async def connect(self) -> bool:
        try:
            ws_url = self._hub_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
            self._ws = await aiohttp.ClientSession().ws_connect(ws_url, heartbeat=20)
            self._running = True
            asyncio.create_task(self._receive())
            print(f"[MaiBot桥接] 已连接 {ws_url}")
            return True
        except Exception as e:
            print(f"[MaiBot桥接] 连接失败: {e}")
            return False

    async def disconnect(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def send_message(self, text: str, reply_to: str = "") -> bool:
        return False

    async def _receive(self) -> None:
        msg_count = 0
        while self._running and self._ws:
            try:
                msg = await self._ws.receive()
                msg_count += 1

                if msg_count <= 3:
                    print(f"[MaiBot桥接] 收到消息#{msg_count} type={msg.type}", flush=True)

                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    kind = data.get("kind", "")

                    if kind == "snapshot":
                        print(f"[MaiBot桥接] 收到快照: {len(data.get('events',[]))}条历史", flush=True)
                        continue

                    if kind == "event":
                        evt = data.get("event", {})
                        evt_type = evt.get("type", "unknown")
                        username = evt.get("username", "?")

                        # 过滤: 屏蔽名单 + AI自己的回复 (B站弹幕只有真人观众)
                        if username in self._blocked_users:
                            self._stats["blocked"] += 1
                            continue
                        # 排除系统消息和非B站来源
                        origin = str(data.get("origin", evt.get("origin", "")))
                        if origin not in ("bilibili", "bilibili_live", ""):
                            continue

                        unified = UnifiedMessage(
                            platform="maibot",
                            user=username,
                            user_id=str(evt.get("user_id", "")),
                            text=evt.get("text", evt.get("summary", "")),
                            event_type="gift" if evt_type == "gift" else "chat",
                            monetary_value=float(evt.get("price", 0)),
                            timestamp=evt.get("timestamp", time.time()),
                        )
                        if unified.text:
                            await self._emit(unified)
                            self._stats["messages" if evt_type != "gift" else "gifts"] += 1
                        continue

                    if kind == "pong":
                        continue

                    # 未知类型
                    if self._stats["messages"] == 0:
                        print(f"[MaiBot桥接] 未知消息: {json.dumps(data, ensure_ascii=False)[:200]}", flush=True)

                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    print(f"[MaiBot桥接] WS关闭", flush=True)
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    print(f"[MaiBot桥接] WS错误", flush=True)
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSING:
                    continue
                else:
                    if msg_count <= 3:
                        raw = str(msg.data)[:100] if msg.data else '(empty)'
                        print(f"[MaiBot桥接] 未处理消息 type={msg.type} data={raw}", flush=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                if msg_count <= 3:
                    print(f"[MaiBot桥接] 异常: {e}", flush=True)

    @property
    def stats(self):
        return dict(self._stats)
