"""50轮超长上下文全链路双模型压力测试"""

import os, sys, asyncio, time, json
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from src.main import AIStreamer

# 50条密集消息: 8个观众, 多话题, 游戏/技术/社会/情绪/送礼/外语
MSGS = [
    (
        "R01",
        "u1",
        "小明",
        "主播终于开播了！今天打Apex还是MC啊？我从早上就开始等了",
        1,
        1,
    ),
    ("R02", "u2", "小红", "新来的举个爪！这主播主要玩啥的，有老粉介绍一下吗", 0, 1),
    ("R03", "u1", "小明", "小红我跟你说这主播贼有意思，上次拿木剑打BOSS笑死我了", 1, 0),
    ("R04", "u3", "老张", "木剑那把我全程看的，弹幕全在刷绷不住了", 0, 0),
    ("R05", "u2", "小红", "哈哈哈哈木剑打BOSS是什么神仙操作，有切片吗我想看", 0, 1),
    (
        "R06",
        "u4",
        "技术宅",
        "主播我问下新版本法师符文到底带什么，电刑和彗星都试了不行",
        0,
        1,
    ),
    ("R07", "u5", "喷子", "菜就多练别找借口，木剑打不过说明你游戏理解有问题", 1, 0),
    ("R08", "u1", "小明", "喷子哥你行你上啊，主播至少敢播，你只会在弹幕指点江山", 1, 0),
    ("R09", "u6", "老板", "", 0, 0),
    ("R10", "u4", "技术宅", "别吵了，主播你倒是说说战士和法师现在到底哪个强", 0, 1),
    (
        "R11",
        "u7",
        "铁粉",
        "来了来了，上次你推荐的龙鳞剑确实好用！还有别的装备推荐吗",
        1,
        1,
    ),
    ("R12", "u8", "游戏宅", "龙鳞剑后期不行，凤凰之刃才是版本答案，不信去看胜率", 0, 0),
    ("R13", "u7", "铁粉", "凤凰太贵了前期出不起，合成路径也拉胯，不如龙鳞平滑", 1, 0),
    ("R14", "u1", "小明", "你们聊装备的能不能去论坛，我是来看操作的", 1, 0),
    ("R15", "u9", "路人甲", "刚进来，这直播间是在聊游戏还是在上课，好热闹", 0, 1),
    ("R16", "u10", "路人乙", "主播你玩LOL吗还是只播Apex，纯FPS主播可不多见了", 0, 1),
    ("R17", "u2", "小红", "主播你是不是不看弹幕了，我们聊这么久你一句都不回", 1, 1),
    ("R18", "u5", "喷子", "看吧我都说了AI主播跟不上弹幕，播什么播", 1, 0),
    ("R19", "u11", "吃瓜", "路过吃瓜，这弹幕比游戏精彩，你们继续我嗑瓜子", 0, 0),
    (
        "R20",
        "u4",
        "技术宅",
        "主播别理喷子，正经问新版本装备改动是不是变相削弱了AP",
        0,
        1,
    ),
    (
        "R21",
        "u7",
        "铁粉",
        "AP确实被砍了，AD也没好到哪去，这版本坦克天下，设计师脑抽",
        1,
        0,
    ),
    ("R22", "u3", "老张", "别骂设计师，每个版本都有人喊弱，适应版本才是本事", 0, 0),
    (
        "R23",
        "u12",
        "职业吹",
        "职业选手一天练12小时，普通玩家哪有这时间，别说风凉话",
        0,
        0,
    ),
    ("R24", "u6", "老板", "", 0, 0),
    ("R25", "u2", "小红", "哇第二个飞机！老板大气，主播还不赶紧谢老板", 1, 0),
    ("R26", "u13", "萌新", "主播我是刚玩的新手，有什么入门建议比如先练什么英雄", 1, 1),
    ("R27", "u14", "暴躁", "新手别玩AD别玩刺客，先练坦克挨打，死多了自然就会了", 0, 0),
    (
        "R28",
        "u13",
        "萌新",
        "坦克太无聊了吧，我想玩有点操作感又不难的那种有推荐吗",
        1,
        1,
    ),
    ("R29", "u1", "小明", "主播你是不是掉线了怎么这么久不说话", 1, 1),
    (
        "R30",
        "u15",
        "好奇",
        "话说主播你到底是AI还是真人，我朋友说是AI我觉得不太像",
        1,
        1,
    ),
    ("R31", "u4", "技术宅", "主播我刚试了你说的方法确实有用，AP中期伤害上来了", 0, 0),
    ("R32", "u2", "小红", "技术宅你试的啥能不能分享一下，我也在练法师", 0, 1),
    ("R33", "u4", "技术宅", "就是主Q副E，先出面具再法穿鞋，主播刚才说的", 0, 0),
    ("R34", "u7", "铁粉", "主播你推荐的装备我都试了，龙鳞前期凤凰后期，完美过渡", 1, 0),
    ("R35", "u16", "新观众", "刚来请问主播一般在哪个段位，白金还是钻石", 0, 1),
    ("R36", "u1", "小明", "主播最高上过钻石，不过最近掉到白金了哈哈哈哈", 1, 0),
    ("R37", "u17", "英语哥", "hey bro what server do you play on? Asia or NA?", 0, 1),
    ("R38", "u18", "日语哥", "主播日本語わかる？日本人だけど見に来た", 0, 1),
    ("R39", "u5", "喷子", "又是英语又是日语，这直播间成分复杂", 1, 0),
    (
        "R40",
        "u19",
        "哲学",
        "你们说如果AI主播真的有自我意识了，它会觉得自己是工具还是生命",
        0,
        1,
    ),
    ("R41", "u1", "小明", "哲学家来了，主播你怎么看，你觉得自己是工具还是生命", 1, 1),
    ("R42", "u19", "哲学", "不是杠，我是真好奇，现在AI发展这么快", 0, 0),
    ("R43", "u3", "老张", "别聊哲学了，主播快开游戏，这都聊了十分钟了还没进游戏", 0, 0),
    ("R44", "u6", "老板", "", 0, 0),
    ("R45", "u2", "小红", "第三个飞机了！老板这是要把主播包养了吗笑死", 1, 0),
    (
        "R46",
        "u13",
        "萌新",
        "主播我刚练了你推荐的英雄确实好用，现在已经能单杀BOSS了",
        1,
        0,
    ),
    ("R47", "u7", "铁粉", "萌新进步快啊，我当初练了一个月才敢打BOSS", 1, 0),
    ("R48", "u20", "夜猫子", "凌晨三点还在播，主播不用睡觉的吗，哦对你是AI", 0, 0),
    ("R49", "u1", "小明", "不行了我要睡了，主播明天还播吗，播的话我定个闹钟", 1, 1),
    ("R50", "u2", "小红", "我也睡了，主播晚安！今天的直播很精彩", 1, 0),
]


async def main():
    s = AIStreamer()
    s._s1._client._mock_mode = False
    s._s1._client._base_url = "http://localhost:9060"
    s._s2._mock_mode = False
    t0 = time.perf_counter()
    await s.start()
    startup = (time.perf_counter() - t0) * 1000

    print(f"STARTUP: {startup:.0f}ms")
    print(f"{'=' * 80}")
    print(
        f"{'Rd':4s} {'User':6s} {'R':1s} {'Total':>6s} {'S2':>6s} {'V':>3s} {'T':>3s} {'Reply'}"
    )
    print(f"{'=' * 80}")

    results = []
    ctx_chars = 0

    for rid, uid, name, text, at, q in MSGS:
        msg = {
            "user": name,
            "user_id": uid,
            "text": text,
            "mentioned_bot": bool(at),
            "is_question": bool(q),
        }
        if "老板" in name:
            msg["event_type"] = "gift"
            msg["price"] = 100

        ctx_chars += len(text)
        t1 = time.perf_counter()
        reply = await s.handle_message(msg)
        total_ms = (time.perf_counter() - t1) * 1000

        s2_lat = 0
        if s._reply_history:
            s2_lat = s._reply_history[-1].s2_latency_ms

        rep_flag = "R" if (reply and len(reply or "") > 1) else "-"
        results.append(
            {
                "rid": rid,
                "replied": rep_flag == "R",
                "total_ms": total_ms,
                "s2_lat": s2_lat,
                "ctx_chars": ctx_chars,
                "viewers": s._l2.viewer_count,
                "threads": s._threads.total_count,
            }
        )

        reply_str = str(reply)[:45] if reply else "None"
        print(
            f"{rid:4s} {name:6s} {rep_flag:1s} {total_ms:6.0f} {s2_lat:6.0f} "
            f"{s._l2.viewer_count:3d} {s._threads.total_count:3d} {reply_str}"
        )

    await s.stop()

    # ── 报告 ──
    replied = [r for r in results if r["replied"]]
    silent = [r for r in results if not r["replied"]]

    print(f"\n{'=' * 80}")
    print(f"FULL REPORT")
    print(f"{'=' * 80}")
    print(f"Total messages: {len(results)}")
    print(
        f"Total context:  {ctx_chars} chars ({ctx_chars / len(results):.0f} chars/msg avg)"
    )
    print(
        f"Reply rate:     {len(replied)}/{len(results)} ({len(replied) / len(results) * 100:.0f}%)"
    )
    print()

    if replied:
        totals = [r["total_ms"] for r in replied]
        s2s = [r["s2_lat"] for r in replied if r["s2_lat"] > 0]
        print(f"-- Reply Latency --")
        print(
            f"  Total: avg={sum(totals) / len(totals):.0f}ms min={min(totals):.0f}ms max={max(totals):.0f}ms"
        )
        if s2s:
            print(
                f"  S2:    avg={sum(s2s) / len(s2s):.0f}ms min={min(s2s):.0f}ms max={max(s2s):.0f}ms"
            )

    if silent:
        silents = [r["total_ms"] for r in silent]
        print(f"\n-- S1 Silent Decision --")
        print(
            f"  avg={sum(silents) / len(silents):.0f}ms min={min(silents):.0f}ms max={max(silents):.0f}ms"
        )

    # Context effect
    if len(replied) >= 4:
        first_half = replied[: len(replied) // 2]
        last_half = replied[len(replied) // 2 :]
        avg_first = sum(r["total_ms"] for r in first_half) / len(first_half)
        avg_last = sum(r["total_ms"] for r in last_half) / len(last_half)
        delta = (avg_last - avg_first) / avg_first * 100
        print(f"\n-- Context Growth Effect --")
        print(f"  First {len(first_half)} replies: avg={avg_first:.0f}ms")
        print(f"  Last  {len(last_half)} replies: avg={avg_last:.0f}ms")
        print(f"  Delta: {delta:+.0f}%")

    # Latency vs context scatter
    print(f"\n-- Latency vs Context (reply rounds only) --")
    for r in replied:
        bar = "#" * max(1, int(r["total_ms"] / 400))
        print(
            f"  {r['rid']:4s} ctx={r['ctx_chars']:4d}ch {bar} {r['total_ms']:.0f}ms (s2={r['s2_lat']:.0f}ms)"
        )

    print(
        f"\nFinal State: {s._l2.viewer_count} viewers, "
        f"{s._threads.total_count} threads, "
        f"{s._wm.reply_count} recorded replies"
    )


asyncio.run(main())
