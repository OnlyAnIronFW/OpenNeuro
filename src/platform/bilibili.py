"""B站直播弹幕适配器 — 多WS并行连接+去重 (只收不发)"""

import asyncio
import json
import struct
import time
import zlib
from typing import Optional, Dict, Any, List

import aiohttp

from .base import PlatformAdapter, UnifiedMessage

OP_HEARTBEAT = 2
OP_MESSAGE = 5
OP_AUTH = 7
OP_AUTH_REPLY = 8
PROTO_ZLIB = 2
HEADER_SIZE = 16
HEADER_STRUCT = struct.Struct(">IHHII")
DEFAULT_WS_URL = "wss://broadcastlv.chat.bilibili.com/sub"


class BilibiliAdapter(PlatformAdapter):
    """B站直播弹幕接收器 — 多路WS并行+去重"""

    def __init__(self, room_id: int, mock_mode: bool = False):
        super().__init__("bilibili")
        self._room_id = room_id
        self._mock_mode = mock_mode
        self._mock_messages: List[Dict] = []
        self._session = None
        self._ws_connections = []
        self._running = False
        self._auth_ready = False
        self._seen_ids: set = set()
        self._api_token = ""
        self._stats = {"messages": 0, "gifts": 0, "errors": 0}

    # ── Mock ──────────────────────────────────────────

    def load_mock_messages(self, messages: List[Dict]) -> None:
        self._mock_messages = list(messages)

    async def _mock_loop(self) -> None:
        import random
        for msg in list(self._mock_messages):
            if not self._running: break
            await self._emit(self.normalize(msg))
            self._stats["messages" if msg.get("cmd") != "SEND_GIFT" else "gifts"] += 1
            await asyncio.sleep(random.uniform(0.3, 2.0))

    # ── 生命周期 ───────────────────────────────────────

    async def connect(self) -> bool:
        if self._mock_mode:
            self._running = True
            asyncio.create_task(self._mock_loop())
            return True

        try:
            # getConf: 获取token和host列表
            token = ""
            hosts = [DEFAULT_WS_URL]
            try:
                h = {"Accept": "application/json", "Referer": f"https://live.bilibili.com/{self._room_id}/",
                     "User-Agent": "Mozilla/5.0"}
                async with aiohttp.ClientSession(headers=h) as s:
                    async with s.get(
                        "https://api.live.bilibili.com/room/v1/Danmu/getConf",
                        params={"room_id": str(self._room_id), "platform": "pc", "player": "web"}, timeout=10
                    ) as r:
                        conf = await r.json(content_type=None)
                    if conf.get("code") == 0:
                        token = conf["data"]["token"]
                        hosts = [f'wss://{x["host"]}:{x["wss_port"]}/sub'
                                 for x in conf["data"]["host_server_list"]]
                        hosts.append(DEFAULT_WS_URL)
            except Exception:
                pass

            self._api_token = token
            self._session = aiohttp.ClientSession()
            self._running = True
            self._auth_ready = False
            self._seen_ids.clear()

            # 多路WS并行连接
            auth_payload = json.dumps({
                "uid": 0, "roomid": self._room_id,
                "protover": PROTO_ZLIB, "platform": "web", "type": 2,
                **({"key": token} if token else {}),
            }, ensure_ascii=False, separators=(",", ":")).encode()
            auth_pkt = HEADER_STRUCT.pack(16+len(auth_payload), 16, 1, OP_AUTH, 1) + auth_payload

            self._ws_connections = []
            for ws_url in hosts[:3]:
                try:
                    ws = await self._session.ws_connect(ws_url, timeout=5, heartbeat=None)
                    self._ws_connections.append(ws)
                    await ws.send_bytes(auth_pkt)
                    asyncio.create_task(self._heartbeat(ws))
                    asyncio.create_task(self._receive(ws))
                except Exception:
                    pass

            if not self._ws_connections:
                raise Exception("所有WS连接失败")

            for _ in range(30):
                if self._auth_ready: break
                await asyncio.sleep(0.1)

            print(f"[B站] 已连接房间{self._room_id} ({len(self._ws_connections)}路WS)")
            return True
        except Exception as e:
            print(f"[B站] 连接失败: {e}")
            return False

    async def disconnect(self) -> None:
        self._running = False
        for ws in self._ws_connections:
            try: await ws.close()
            except: pass
        self._ws_connections = []
        if self._session:
            await self._session.close()
            self._session = None

    async def send_message(self, text: str, reply_to: str = "") -> bool:
        return False

    # ── 收发 ───────────────────────────────────────────

    async def _heartbeat(self, ws) -> None:
        while self._running and not ws.closed:
            await asyncio.sleep(30)
            try: await ws.send_bytes(HEADER_STRUCT.pack(16, 16, 1, OP_HEARTBEAT, 1))
            except: break

    async def _receive(self, ws) -> None:
        last_data = time.time()
        while self._running and not ws.closed:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                if msg.type != aiohttp.WSMsgType.BINARY:
                    if msg.type == aiohttp.WSMsgType.CLOSED: break
                    continue
                last_data = time.time()

                # 解析
                for raw in self._parse(msg.data):
                    if isinstance(raw, dict) and raw.get("code") == 0 and not raw.get("cmd"):
                        self._auth_ready = True; continue

                    # 去重
                    eid = str(hash(json.dumps(raw, sort_keys=True, default=str)))
                    if eid in self._seen_ids: continue
                    self._seen_ids.add(eid)
                    if len(self._seen_ids) > 5000:
                        self._seen_ids = set(list(self._seen_ids)[-2000:])

                    unified = self._normalize(raw)
                    if unified:
                        await self._emit(unified)
                        self._stats["messages" if raw.get("cmd") != "SEND_GIFT" else "gifts"] += 1
            except asyncio.TimeoutError:
                if time.time() - last_data > 60: break
            except asyncio.CancelledError: break
            except Exception: self._stats["errors"] += 1

        # 断线重连
        if self._running:
            await asyncio.sleep(3)
            if self._running:
                asyncio.create_task(self._auto_reconnect())

    async def _auto_reconnect(self) -> None:
        while self._running:
            try:
                for ws in self._ws_connections:
                    try: await ws.close()
                    except: pass
                self._ws_connections = []
                self._auth_ready = False
                if await self.connect(): return
            except asyncio.CancelledError: break
            except Exception: pass
            await asyncio.sleep(5)

    # ── 解析 ───────────────────────────────────────────

    def _parse(self, data: bytes) -> List[Dict]:
        results = []
        off = 0
        while off + HEADER_SIZE <= len(data):
            tl, hl, ver, op, _ = HEADER_STRUCT.unpack_from(data, off)
            if tl < hl or hl < HEADER_SIZE or off + tl > len(data): break
            body = data[off+hl:off+tl]
            if op == OP_MESSAGE and ver == PROTO_ZLIB and body:
                try: results.extend(self._parse(zlib.decompress(body)))
                except zlib.error: pass
            elif op == OP_AUTH_REPLY:
                try: results.append(json.loads(body.decode(errors="replace")))
                except: pass
            elif op == OP_MESSAGE and body:
                for line in body.decode(errors="replace").split("\n"):
                    if line.strip().startswith("{"):
                        try: results.append(json.loads(line.strip()))
                        except: pass
            off += tl
        return results

    @staticmethod
    def _normalize(raw: Dict) -> Optional[UnifiedMessage]:
        cmd = str(raw.get("cmd") or "").split(":", 1)[0]
        if cmd == "DANMU_MSG":
            info = raw.get("info")
            if not isinstance(info, list) or len(info) < 3: return None
            t = str(info[1] or "").strip()
            if not t: return None
            u = info[2] if isinstance(info[2], list) else []
            return UnifiedMessage(platform="bilibili", user=str(u[1] if len(u) > 1 else "?"),
                                  user_id=str(u[0] if u else ""), text=t,
                                  timestamp=info[0][4]/1000 if info[0] and len(info[0])>4 else time.time())
        if cmd == "SEND_GIFT":
            d = raw.get("data")
            if not isinstance(d, dict): return None
            return UnifiedMessage(platform="bilibili", user=str(d.get("uname") or ""),
                                  user_id=str(d.get("uid") or ""), event_type="gift",
                                  text=f"送了{d.get('num',1)}个{d.get('giftName','礼物')}",
                                  monetary_value=float(d.get("price",0))/1000, timestamp=time.time())
        if cmd.startswith("INTERACT_WORD") or cmd == "WELCOME":
            d = raw.get("data")
            if not isinstance(d, dict): return None
            return UnifiedMessage(platform="bilibili",
                                  user=str(d.get("uname") or d.get("username") or ""),
                                  user_id=str(d.get("uid") or ""), text="进入直播间", timestamp=time.time())
        return None

    # ── 昵称缓存 ──────────────────────────────────────

    _name_cache: Dict[str, str] = {}

    @classmethod
    async def resolve_name(cls, uid: str) -> str:
        if not uid or not uid.isdigit(): return uid
        if uid in cls._name_cache: return cls._name_cache[uid]
        import re
        try:
            h = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            async with aiohttp.ClientSession(headers=h) as s:
                async with s.get(f'https://space.bilibili.com/{uid}', timeout=5) as r:
                    html = await r.text()
            m = re.search(r'<title>\s*(.+?)\s*的个人空间', html)
            if m:
                name = m.group(1).strip()
                if name and len(name) < 30 and '的个人空间' not in name:
                    cls._name_cache[uid] = name; return name
        except Exception: pass
        cls._name_cache[uid] = f'用户{uid[-4:]}'
        return cls._name_cache[uid]

    @property
    def stats(self) -> Dict:
        return dict(self._stats)
