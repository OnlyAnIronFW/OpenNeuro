"""全链路双模型实测: B站弹幕 → S1(MiniCPM) → S2(DeepSeek) → 回复"""

import asyncio, os, sys, time, json
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from src.main import AIStreamer
from src.platform.bilibili import BilibiliAdapter

LOG = []


def log(msg):
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] {msg}"
    print(line)
    LOG.append(line)


async def main():
    log("=" * 60)
    log("全链路双模型实测: B站弹幕 → MiniCPM S1 → DeepSeek S2")
    log(f"房间: 4538234 | 时长: 5分钟")
    log("=" * 60)

    # 1. 启动 AI Streamer (真实双模型)
    log("启动 AI Streamer (S1=real MiniCPM, S2=real DeepSeek)...")
    s = AIStreamer()
    s._s1._client._mock_mode = False
    s._s1._client._base_url = "http://localhost:9060"
    s._s2._mock_mode = False
    await s.start()
    log(f"S1健康: {await s._s1._client.is_healthy()}")
    log(f"AIStreamer已启动")

    # 2. 连接B站弹幕
    log("连接B站房间4538234...")
    adapter = BilibiliAdapter(room_id=4538234)

    total_danmaku = 0
    total_replies = 0
    total_skipped = 0

    async def on_danmaku(msg):
        nonlocal total_danmaku, total_replies, total_skipped
        total_danmaku += 1

        # 格式化弹幕
        danmaku_text = f"[{msg.user}] {msg.text}"
        log(f"弹幕 #{total_danmaku}: {danmaku_text[:80]}")

        # 喂给AI
        t0 = time.perf_counter()
        reply = await s.handle_message(
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
            log(f'  → 回复 #{total_replies}: "{reply[:60]}" ({lat:.0f}ms)')
        else:
            total_skipped += 1
            log(f"  → 不回复 ({lat:.0f}ms)")

    adapter.on_message(on_danmaku)

    ok = await adapter.connect()
    if not ok:
        log("B站连接失败，切换到Mock模式测试")
        adapter = BilibiliAdapter(room_id=4538234, mock_mode=True)
        adapter.load_mock_messages(
            [
                {
                    "cmd": "DANMU_MSG",
                    "user": "小明",
                    "user_id": "u1",
                    "text": "主播今天玩什么游戏",
                },
                {
                    "cmd": "DANMU_MSG",
                    "user": "小红",
                    "user_id": "u2",
                    "text": "刚来，主播好",
                },
            ]
        )
        await adapter.connect()

    # 3. 运行5分钟
    log(f"开始监听 (5分钟)...")
    log("")
    await asyncio.sleep(300)

    # 4. 收尾
    await adapter.disconnect()
    await s.stop()

    log("")
    log("=" * 60)
    log("测试完成")
    log(f"弹幕总数: {total_danmaku}")
    log(f"AI回复: {total_replies}")
    log(f"跳过: {total_skipped}")
    if total_danmaku > 0:
        log(f"回复率: {total_replies / total_danmaku * 100:.0f}%")
    log(f"观众数: {s._l2.viewer_count}")
    log(f"线程数: {s._threads.total_count}")
    log(f"日志路径: data/logs/main.log")
    log("=" * 60)

    # 保存完整记录
    with open("data/full_live_test_log.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "room_id": 4538234,
                "duration_minutes": 5,
                "total_danmaku": total_danmaku,
                "total_replies": total_replies,
                "total_skipped": total_skipped,
                "viewers": s._l2.viewer_count,
                "threads": s._threads.total_count,
                "log": LOG,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    log("完整记录已保存到 data/full_live_test_log.json")


asyncio.run(main())
