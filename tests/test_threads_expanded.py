"""线程管理器 扩展测试 + 真实API多线程"""

import os, sys, time, asyncio, threading
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

p = f = 0


def check(name, condition, detail=""):
    global p, f
    if condition:
        p += 1
        print(f"  [OK] {name}{' — ' + detail if detail else ''}")
    else:
        f += 1
        print(f"  [FAIL] {name}{' — ' + detail if detail else ''}")


# ═══════════════════════════════════════════════════
# E1: 关键词保序
# ═══════════════════════════════════════════════════
print("\n── E1: 关键词 ──")
from src.threads.manager import ThreadManager

tm = ThreadManager()
tid = tm.on_message({"user": "a", "user_id": "ua", "text": "战士装备推荐"})
t = tm._threads[tid]
check("E1.1 keywords present", len(t.topic_keywords) > 0, str(t.topic_keywords[:5]))
check("E1.2 keyword includes 战士", any("战士" in k for k in t.topic_keywords))

# ═══════════════════════════════════════════════════
# E2: 参与者隔离
# ═══════════════════════════════════════════════════
print("\n── E2: 参与者 ──")
tm2 = ThreadManager()
tm2.on_message({"user": "小明", "user_id": "uid_xm", "text": "hello"})
tm2.on_message({"user": "小红", "user_id": "uid_xh", "text": "hi"})
t2 = tm2._threads.get(
    tm2.on_message({"user": "小明", "user_id": "uid_xm", "text": "again"})
)
# participants 应有 uid_xm, uid_xh, 小明, 小红
check("E2.1 has user_ids", "uid_xm" in t2.participants)
check("E2.2 has display_names", "小明" in t2.participants)
# 忠诚度计算应跳过 display_name
pri = t2.priority
check("E2.3 priority calc works", pri > 0, f"pri={pri}")

# ═══════════════════════════════════════════════════
# E3: 并发压力
# ═══════════════════════════════════════════════════
print("\n── E3: 并发 ──")
tm3 = ThreadManager()
errors = []


def worker(wid):
    try:
        for i in range(100):
            tm3.on_message(
                {
                    "user": f"w{wid}_{i}",
                    "user_id": f"uid_w{wid}_{i}",
                    "text": f"msg_{wid}_{i}",
                }
            )
    except Exception as e:
        errors.append(str(e))


threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("E3.1 1000 concurrent messages", len(errors) == 0, f"errors={len(errors)}")
check("E3.2 thread count after", tm3.total_count > 0, f"total={tm3.total_count}")

# ═══════════════════════════════════════════════════
# E4: 边界
# ═══════════════════════════════════════════════════
print("\n── E4: 边界 ──")
tm4 = ThreadManager()
# 空消息
tid_e = tm4.on_message({"user": "x", "text": ""})
check("E4.1 empty text ok", tid_e is not None)
# 无user_id
tid_e2 = tm4.on_message({"text": "hello"})
check("E4.2 no user_id ok", tid_e2 is not None)
# None text
tid_e3 = tm4.on_message({"user": "x", "text": None})
check("E4.3 None text ok", tid_e3 is not None)

# ═══════════════════════════════════════════════════
# E5: 真实API多线程 (S1=mock, S2=real)
# ═══════════════════════════════════════════════════
print("\n── E5: 真实API多线程 ──")
from src.main import AIStreamer


async def test_real():
    s = AIStreamer()
    s._s1._client._mock_mode = True
    s._s2._mock_mode = False  # real DeepSeek
    await s.start()

    # 模拟5个观众同时聊
    viewers = [
        ("小明", "uid_1", "主播，法师怎么加点"),
        ("小红", "uid_2", "今天播什么游戏"),
        ("老张", "uid_3", "主播好菜啊哈哈哈哈"),
        ("小刚", "uid_4", "新版本更新了啥"),
        ("路人", "uid_5", "hello"),
    ]

    for name, uid, text in viewers:
        s._s1._client.set_mock_responses(
            [f"<|Start-Speaking confidence=0.6|> 回复{name}关于话题的消息"]
        )
        t0 = time.perf_counter()
        reply = await s.handle_message(
            {
                "user": name,
                "user_id": uid,
                "text": text,
                "mentioned_bot": True if uid == "uid_1" else False,
                "is_question": "?" in text,
            }
        )
        lat = (time.perf_counter() - t0) * 1000
        check(
            f"E5.{name}",
            reply is not None and len(reply or "") > 0,
            f"'{str(reply)[:40]}' {lat:.0f}ms",
        )

    # 验证线程分布
    snap = s._threads.snapshot()
    total_threads = len(snap)
    check(
        "E5.6 thread distribution",
        total_threads >= 1,
        f"threads={total_threads} snap={[(t['id'], t['topic_label'][:15]) for t in snap]}",
    )

    # 线程优先级
    next_t = s._threads.next_to_reply()
    check(
        "E5.7 priority queue",
        next_t is not None,
        f"next={next_t.thread_id} pri={next_t.priority}",
    )

    await s.stop()


asyncio.run(test_real())

print(f"\nTOTAL: {p + f} | PASS={p} | FAIL={f}")
if f:
    sys.exit(1)
print("EXPANDED THREAD TESTS: ALL PASSED")
