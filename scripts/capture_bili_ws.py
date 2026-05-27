#!/usr/bin/env python
"""抓取 B站 WebSocket auth 包 — 用于调试适配器协议

用法: python scripts/capture_bili_ws.py [房间号]
输出: data/bili_ws_capture.json (抓包数据)
"""

import asyncio
import json
import os
import sys
from pathlib import Path

CAPTURE_FILE = Path("data/bili_ws_capture.json")


async def capture(room_id: int = 7777):
    """用 Playwright 打开 B站直播间, 抓取 WebSocket 通信"""
    from playwright.async_api import async_playwright

    captured = {"room_id": room_id, "ws_url": "", "auth_sent": "", "auth_reply": "",
                "messages": [], "error": ""}

    # 加载 Cookie
    cookie_data = {}
    cookie_file = Path("data/bili_cookie.json")
    if cookie_file.exists():
        cookie_data = json.loads(cookie_file.read_text(encoding="utf-8"))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()

        # 注入 B站 Cookie
        if cookie_data.get("sessdata"):
            await context.add_cookies([{
                "name": "SESSDATA",
                "value": cookie_data["sessdata"],
                "domain": ".bilibili.com",
                "path": "/",
            }])
            print("[+] 已注入 SESSDATA Cookie")

        page = await context.new_page()

        # 监听所有 WebSocket
        ws_frames = []

        def on_ws(ws):
            ws_url = ws.url
            if "chat.bilibili.com" in ws_url or "broadcastlv" in ws_url:
                captured["ws_url"] = ws_url
                print(f"[+] 捕获 WebSocket: {ws_url}")

                def on_frame_sent(payload):
                    # Playwright: payload 可能是 str 或 bytes
                    data = payload.encode() if isinstance(payload, str) else payload
                    if data and len(data) > 5:
                        ws_frames.append({"direction": "sent", "hex": data.hex() if isinstance(data, bytes) else data, "len": len(data)})
                        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(payload)
                        if "roomid" in text or "uid" in text:
                            captured["auth_sent"] = text

                def on_frame_received(payload):
                    data = payload.encode() if isinstance(payload, str) else payload
                    if data and len(data) > 5:
                        ws_frames.append({"direction": "recv", "hex": data.hex() if isinstance(data, bytes) else data, "len": len(data)})
                        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(payload)
                        if "code" in text and not captured["auth_reply"]:
                            captured["auth_reply"] = text
                        if "DANMU_MSG" in text:
                            captured["messages"].append(text[:200])

                ws.on("framesent", on_frame_sent)
                ws.on("framereceived", on_frame_received)

        page.on("websocket", on_ws)

        # 打开直播间
        url = f"https://live.bilibili.com/{room_id}"
        print(f"[*] 打开 {url} ...")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # 等待 WebSocket 连接 + 弹幕到达 (最多 60 秒)
        print("[*] 等待弹幕数据... (60秒)")
        for i in range(60):
            await asyncio.sleep(1)
            if captured["auth_sent"] and len(captured["messages"]) >= 3:
                print(f"[+] 已捕获足够数据 (auth + {len(captured['messages'])} 条弹幕)")
                break
        else:
            if not captured["auth_sent"]:
                captured["error"] = "未捕获到WebSocket auth包 (可能需要登录或房间无弹幕)"

        await browser.close()

    # 保存结果
    captured["frames_count"] = len(ws_frames)
    captured["frames_sample"] = ws_frames[:30]  # 前30帧
    CAPTURE_FILE.write_text(json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")

    # 打印结果
    print(f"\n{'='*60}")
    print(f"结果已保存到 {CAPTURE_FILE}")
    print(f"{'='*60}")
    print(f"WS URL: {captured['ws_url']}")
    print(f"Auth包 (sent): {captured['auth_sent'][:200] if captured['auth_sent'] else '未捕获'}")
    print(f"Auth回复: {captured['auth_reply'][:200] if captured['auth_reply'] else '未捕获'}")
    print(f"弹幕数: {len(captured['messages'])}")
    if captured["error"]:
        print(f"错误: {captured['error']}")

    return captured


if __name__ == "__main__":
    room = int(sys.argv[1]) if len(sys.argv) > 1 else 7777
    asyncio.run(capture(room))
