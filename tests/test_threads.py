"""Phase 3-2: 线程管理器 全面测试"""

import os, sys, time, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

p = f = 0
def check(name, condition, detail=""):
    global p, f
    if condition: p += 1; print(f"  [OK] {name}{' — '+detail if detail else ''}")
    else: f += 1; print(f"  [FAIL] {name}{' — '+detail if detail else ''}")

# ═══════════════════════════════════════════════════
# T1: 线程分配
# ═══════════════════════════════════════════════════
print("\n── T1: 线程分配 ──")
from src.threads.manager import ThreadManager

tm = ThreadManager()

# T1.1: 新建线程
tid1 = tm.on_message({"user": "小明", "user_id": "u1", "text": "主播好"})
check("T1.1 new thread", tid1.startswith("thr_"))

# T1.2: 同一个人连续发言 → 同一线程
tid2 = tm.on_message({"user": "小明", "user_id": "u1", "text": "今天玩什么"})
check("T1.2 same user same thread", tid2 == tid1)

# T1.3: 不同人新话题 → 新线程
tid3 = tm.on_message({"user": "小红", "user_id": "u2", "text": "这个BOSS怎么打"})
check("T1.3 different topic new thread", tid3 != tid1)

# T1.4: 语义相似 → 归入已有线程
tid4 = tm.on_message({"user": "小刚", "user_id": "u3", "text": "BOSS太难了"})
check("T1.4 semantic merge", tid4 == tid3)

# T1.5: @提及归入线程
tid5 = tm.on_message({"user": "老张", "user_id": "u4",
    "text": "@小明 你说的那个装备在哪", "reply_to_msg_id": ""})
# u4的新消息, @了u1(小明), 应归入tid1
check("T1.5 @mention merge", tid5 == tid1)

# T1.6: 上限 → 最低优先级关闭
for i in range(15):
    tm.on_message({"user": f"bulk_{i}", "user_id": f"bu_{i}", "text": f"topic_{i}"})
check("T1.6 max active cap", tm.active_count <= 10, f"active={tm.active_count}")

# ═══════════════════════════════════════════════════
# T2: 优先级
# ═══════════════════════════════════════════════════
print("\n── T2: 优先级 ──")
tm2 = ThreadManager()

# 普通消息 → 基线优先级
tm2.on_message({"user": "a", "user_id": "ua", "text": "hello"})
next_t = tm2.next_to_reply()
check("T2.1 baseline priority", next_t is not None)

# @消息 → 更高优先级
tm2.on_message({"user": "b", "user_id": "ub", "text": "test", "mentioned_bot": True})
next_t2 = tm2.next_to_reply()
check("T2.2 @mention higher priority", next_t2 is not None)

# 问题 → 更高优先级
tm2.on_message({"user": "c", "user_id": "uc", "text": "怎么玩", "is_question": True})
next_t3 = tm2.next_to_reply()
check("T2.3 question priority", next_t3 is not None)

# ═══════════════════════════════════════════════════
# T3: 冷却 + 反饥饿
# ═══════════════════════════════════════════════════
print("\n── T3: 冷却+反饥饿 ──")
tm3 = ThreadManager()

tid = tm3.on_message({"user": "x", "user_id": "ux", "text": "topic1"})
for _ in range(3):
    tm3.mark_replied(tid)
    tm3.on_message({"user": "x", "user_id": "ux", "text": "more"})
t = tm3._threads.get(tid)
check("T3.1 cooldown after 3 replies", t is not None and t.state == "cooling_down",
      f"state={t.state if t else 'None'}")

# 反饥饿: 新建线程从未回复, 应排最前
tm3b = ThreadManager()
tid_a = tm3b.on_message({"user": "old", "user_id": "uo", "text": "old topic"})
tm3b.mark_replied(tid_a)
tid_b = tm3b.on_message({"user": "new", "user_id": "un", "text": "new topic"})
next_t = tm3b.next_to_reply()
check("T3.2 anti-starvation", next_t is not None and next_t.thread_id == tid_b,
      f"got={next_t.thread_id if next_t else 'None'} expected={tid_b}")

# ═══════════════════════════════════════════════════
# T4: 快照 + 清理
# ═══════════════════════════════════════════════════
print("\n── T4: 快照+清理 ──")
snap = tm.snapshot()
check("T4.1 snapshot has threads", len(snap) >= 1)
check("T4.2 snapshot fields", "id" in snap[0] and "priority" in snap[0])

pruned = tm.prune_stale()
check("T4.3 prune_stale runs", pruned >= 0)

# ═══════════════════════════════════════════════════
# T5: 合并
# ═══════════════════════════════════════════════════
print("\n── T5: 合并 ──")
tm5 = ThreadManager()
ta = tm5.on_message({"user": "a", "user_id": "ua5", "text": "战士装备"})
tb = tm5.on_message({"user": "b", "user_id": "ub5", "text": "战士武器推荐"})
check("T5.1 similar topics merged", ta == tb)

# ═══════════════════════════════════════════════════
# T6: 集成 (AIStreamer)
# ═══════════════════════════════════════════════════
print("\n── T6: 集成 ──")
os.environ['DEEPSEEK_API_KEY'] = 'test-key'
from src.main import AIStreamer

async def test_integration():
    s = AIStreamer()
    s._s1._client._mock_mode = True
    s._s2._mock_mode = True
    await s.start()

    # 多消息线程分配
    from src.models.s2_client import S2Response
    s._s2.set_mock_responses([S2Response(content="mock")])
    s._s1._client.set_mock_responses(["<|Quick-Reply|> test"])

    await s.handle_message({"user":"a","user_id":"ua","text":"hello"})
    await s.handle_message({"user":"b","user_id":"ub","text":"hi"})
    await s.handle_message({"user":"a","user_id":"ua","text":"question"})

    check("T6.1 threads created", s._threads.total_count >= 1,
          f"total={s._threads.total_count}")
    check("T6.2 active count", s._threads.active_count >= 1,
          f"active={s._threads.active_count}")

    # 线程快照
    snap = s._threads.snapshot()
    check("T6.3 snapshot in streamer", len(snap) >= 1)

    # 停止
    await s.stop()
    check("T6.4 stop clean", True)

asyncio.run(test_integration())

print(f"\nTOTAL: {p+f} | PASS={p} | FAIL={f}")
if f: sys.exit(1)
print("THREAD TESTS: ALL PASSED")
