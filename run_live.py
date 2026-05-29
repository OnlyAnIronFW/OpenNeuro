"""AI 主播全链路启动脚本 — 统一入口

用法:
    python run_live.py                        # 默认: MaiBot Bridge
    python run_live.py --platform bilibili    # B站 WebSocket 直连
    python run_live.py --platform maibot      # MaiBot Live Hub
"""

import asyncio
import os
import sys
import time
import argparse

from dotenv import load_dotenv

load_dotenv()

MAIBOT_HUB = "http://127.0.0.1:18190"

# 确保在项目目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))


async def main(args=None):
    if args is None:
        parser = argparse.ArgumentParser(description="AI Streamer 启动器")
        parser.add_argument(
            "--platform",
            choices=["maibot", "bilibili"],
            default="maibot",
            help="平台适配器: maibot (MaiBot Live Hub) 或 bilibili (WebSocket直连)",
        )
        args = parser.parse_args()

    sys.path.insert(0, ".")
    from src.main import AIStreamer

    platform_name = args.platform
    adapter = None

    print("=" * 60, flush=True)
    print(f"  AI Streamer - 平台: {platform_name}", flush=True)
    print(f"  S1: MiniCPM | S2: DeepSeek", flush=True)
    print("=" * 60, flush=True)
    print(flush=True)

    # 1. 选择并创建平台适配器
    if platform_name == "maibot":
        from src.platform.maibot_bridge import MaiBotBridge

        print(f"[*] 连接 MaiBot Live Hub...", flush=True)
        adapter = MaiBotBridge(hub_url=MAIBOT_HUB)
    elif platform_name == "bilibili":
        from src.platform.bilibili import BilibiliAdapter

        print(f"[*] 连接 B站 WebSocket...", flush=True)
        adapter = BilibiliAdapter(room_id=4538234)
    else:
        print(f"[!] 未知平台: {platform_name}", flush=True)
        return

    # 2. 检查MiniCPM
    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:9060/health", timeout=2)
        print(f"[+] MiniCPM 就绪", flush=True)
    except Exception:
        print(f"[!] MiniCPM 未就绪，请先运行 start_minicpm.bat", flush=True)
        return

    # 3. AI
    print(f"[*] 启动AI...", flush=True)
    streamer = AIStreamer()
    # 强制真实S1 (覆盖__init__中的auto-detection)
    streamer._s1._client._mock_mode = False
    streamer._s1._client._base_url = "http://localhost:9060"
    streamer._s2._mock_mode = False
    try:
        await streamer.start()
        print(f"[+] S1: real MiniCPM | S2: real DeepSeek", flush=True)
    except Exception as e:
        print(f"[!] 启动失败: {e}", flush=True)
        print(f"[!] 回退: S1=Mock, S2=Mock", flush=True)
        streamer._s1._client._mock_mode = True
        streamer._s2._mock_mode = True
        await streamer.start()
        print(f"[+] S1: Mock | S2: Mock", flush=True)

    total_danmaku = 0
    total_replies = 0

    # TTS 引擎 (按需延迟加载)
    tts_engine = None

    async def _ensure_tts():
        nonlocal tts_engine
        if tts_engine is not None:
            return tts_engine
        try:
            from src.tts import get_comni_bridge

            tts_engine = await get_comni_bridge()
            return tts_engine
        except Exception as e:
            print(f"[!] TTS init failed: {e}", flush=True)
            return None

    async def on_msg(msg):
        nonlocal total_danmaku, total_replies
        total_danmaku += 1
        t = time.strftime("%H:%M:%S")

        if msg.event_type == "gift":
            print(f"[{t}] [GIFT] {msg.user}: {msg.text}", flush=True)
        else:
            print(f"[{t}] [{msg.user}] {msg.text}", flush=True)

        # AI 回复
        t0 = time.perf_counter()
        reply = await streamer.handle_message(
            {
                "user": msg.user,
                "user_id": msg.user_id,
                "text": msg.text,
                "mentioned_bot": False,
                "is_question": "?" in msg.text,
                "event_type": msg.event_type,
                "price": msg.monetary_value,
            }
        )
        lat = (time.perf_counter() - t0) * 1000
        if reply and len(reply) > 1:
            total_replies += 1
            print(
                f"  >>> [{streamer._emotion.to_prompt_str()}] {reply} ({lat:.0f}ms)",
                flush=True,
            )
            # TTS 语音输出
            eng = await _ensure_tts()
            if eng and eng.is_ready():
                try:
                    await eng.speak(reply)
                except Exception as e:
                    print(f"  [!] TTS failed: {e}", flush=True)
        else:
            print(f"  --- ({lat:.0f}ms)", flush=True)

    adapter.on_message(on_msg)
    ok = await adapter.connect()
    if not ok:
        print(f"[!] {platform_name} 连接失败", flush=True)
        await streamer.stop()
        return

    print(f"[+] MaiBot桥接已连接", flush=True)
    print(f"[+] 监听中... (Ctrl+C 停止)", flush=True)
    print("=" * 60, flush=True)
    print(flush=True)

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] 停止...", flush=True)
    finally:
        await streamer.stop()
        await adapter.disconnect()
        print(f"[*] 弹幕:{total_danmaku} 回复:{total_replies}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
