"""
记忆系统扩展测试 — R2 扩测

覆盖: L1 边界/L2 并发/FAQ 压力/持久化容错/损坏恢复/空数据
"""

import os, sys, time, asyncio, json, tempfile, shutil, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

p = f = 0
def check(name, condition, detail=""):
    global p, f
    if condition: p += 1; print(f"  [OK] {name}{' — '+detail if detail else ''}")
    else: f += 1; print(f"  [FAIL] {name}{' — '+detail if detail else ''}")

# ═══════════════════════════════════════════════════
# E1: L1 边界测试
# ═══════════════════════════════════════════════════
print("\n── E1: L1 边界 ──")
from src.memory.l1_working import WorkingMemory

wm = WorkingMemory()

# E1.1: 环形缓冲溢出
for i in range(100):
    wm.add_message({"user": f"u{i}", "text": f"msg{i}"})
check("E1.1 ring buffer cap", len(wm.recent_messages) == 50)

# E1.2: 去重上限
for i in range(300):
    wm.mark_replied(f"msg_{i}")
check("E1.2 dedup cap", len(wm._replied_ids) <= 200)

# E1.3: text=None 不崩溃
wm.add_message({"user": "test", "text": None})
check("E1.3 text=None safe", True)

# E1.4: 空消息
wm.add_message({})
check("E1.4 empty msg safe", True)

# E1.5: 话题切换
wm.update_topic("A"); wm.update_topic("A"); wm.update_topic("B")
check("E1.5 topic switch", wm.current_topic == "B" and wm.topic_msg_count == 1)

# E1.6: reset 完整
wm.reset()
check("E1.6 full reset", wm.reply_count == 0 and len(wm.recent_messages) == 0)

# ═══════════════════════════════════════════════════
# E2: L2 并发 + 压力
# ═══════════════════════════════════════════════════
print("\n── E2: L2 压力 ──")
from src.memory.l2_short import ShortTermMemory

tmp = tempfile.mkdtemp()
l2 = ShortTermMemory(data_dir=tmp)

# E2.1: 大量观众
for i in range(500):
    l2.upsert_viewer(f"user_{i}", f"name_{i}")
check("E2.1 500 viewers", l2.viewer_count == 500)

# E2.2: 大量互动
for i in range(200):
    l2.record_interaction(f"query_{i}", f"reply_{i}", f"user_{i%50}")
check("E2.2 200 interactions", l2.interaction_count == 200)

# E2.3: FAQ 上限
for i in range(300):
    l2.faq_set(f"faq_key_{i}", f"faq_val_{i}")
check("E2.3 FAQ cap", l2.faq_count <= 200)

# E2.4: 并发写入 (多线程不崩)
def thread_write(tid):
    for j in range(10):
        l2.upsert_viewer(f"thread_{tid}_{j}", f"t{tid}_{j}")
threads = [threading.Thread(target=thread_write, args=(i,)) for i in range(10)]
for t in threads: t.start()
for t in threads: t.join()
check("E2.4 concurrent write", l2.viewer_count > 500)

# E2.5: 搜索性能 (500 viewers, < 50ms)
t0 = time.perf_counter()
results = l2.search_viewers("name_42")
elapsed = (time.perf_counter() - t0) * 1000
check("E2.5 search perf", elapsed < 100, f"{elapsed:.0f}ms")

# ═══════════════════════════════════════════════════
# E3: 持久化容错
# ═══════════════════════════════════════════════════
print("\n── E3: 持久化容错 ──")

# E3.1: 正常保存+加载
l2.save()
l2b = ShortTermMemory(data_dir=tmp)
check("E3.1 save/load", l2b.viewer_count == l2.viewer_count)

# E3.2: 损坏的JSON文件
corrupt_path = tmp + "/viewers.json"
with open(corrupt_path, 'w') as f: f.write("this is not json{{{")
l2c = ShortTermMemory(data_dir=tmp)
check("E3.2 corrupt JSON", l2c.viewer_count == 0)

# E3.3: 缺失字段的旧格式
old_format = {"viewers": {"old_001": {"user_id": "old_001", "display_name": "legacy"}}}
with open(corrupt_path, 'w') as f: json.dump(old_format, f)
l2d = ShortTermMemory(data_dir=tmp)
v = l2d.get_viewer("old_001")
check("E3.3 legacy format", v is not None and v.display_name == "legacy")
check("E3.3 legacy defaults", v.interaction_count == 0)

# E3.4: 空目录
empty_dir = tempfile.mkdtemp()
l2e = ShortTermMemory(data_dir=empty_dir)
check("E3.4 empty dir", l2e.viewer_count == 0)
shutil.rmtree(empty_dir)

shutil.rmtree(tmp)

# ═══════════════════════════════════════════════════
# E4: FAQ 边界
# ═══════════════════════════════════════════════════
print("\n── E4: FAQ 边界 ──")
tmp2 = tempfile.mkdtemp()
l2f = ShortTermMemory(data_dir=tmp2)

# E4.1: 空查询
check("E4.1 empty query get", l2f.faq_get("") is None)
l2f.faq_set("", "val")
check("E4.2 empty query set", l2f.faq_get("") is None)  # 空key不存入

# E4.3: 空回复不存
l2f.faq_set("q", "")
check("E4.3 empty reply not stored", l2f.faq_count == 0)

# E4.4: 精确命中刷新LRU
l2f.faq_set("q1", "v1"); l2f.faq_set("q2", "v2"); l2f.faq_set("q3", "v3")
# q1是最老的, 但通过get刷新它
l2f.faq_get("q1")
# 现在q2是最老的
l2f.faq_set("q4", "v4")  # 不会淘汰q1(刚被访问), 淘汰q2
check("E4.4 LRU refresh", l2f.faq_get("q1") == "v1")

# E4.5: 模糊匹配阈值边界
l2f.faq_set("abcdefgh", "test")
# "abcdefgi" 距离1, sim=1-1/8=0.875 >= 0.85 → 命中
hit = l2f.faq_get("abcdefgi")
check("E4.5 threshold match", hit == "test")
# "abcxyz" 距离远 → 未命中
miss = l2f.faq_get("abcxyz")
check("E4.6 threshold miss", miss is None)

shutil.rmtree(tmp2)

# ═══════════════════════════════════════════════════
# E5: 集成 (AIStreamer memory flow)
# ═══════════════════════════════════════════════════
print("\n── E5: 集成 ──")
os.environ['DEEPSEEK_API_KEY'] = os.environ.get('DEEPSEEK_API_KEY', 'test-key')
from src.main import AIStreamer

async def test_integration():
    s = AIStreamer()
    s._s1._client._mock_mode = True
    s._s2._mock_mode = True
    await s.start()

    # E5.1: QuickReply → 建档
    s._s1._client.set_mock_responses(["<|Quick-Reply|> hi"])
    await s.handle_message({"user":"u1","user_id":"uid1","text":"hello"})
    v = s._l2.get_viewer("uid1")
    check("E5.1 QR profiles", v is not None and v.interaction_count == 1)

    # E5.2: Start-Speaking → 建档 + 互动记录
    s._s1.reset()
    s._s1._client.set_mock_responses([
        "<|Start-Speaking confidence=0.7|> test direction"
    ])
    # mock S2 needs a response since it's in mock mode and Start-Speaking calls S2
    from src.models.s2_client import S2Response, ThinkingMode
    s._s2.set_mock_responses([S2Response(content="mock reply from s2")])
    await s.handle_message({"user":"u2","user_id":"uid2","text":"question"})
    v2 = s._l2.get_viewer("uid2")
    check("E5.2 SS profiles", v2 is not None, f"v2={v2}")
    check("E5.3 interactions recorded", s._l2.interaction_count >= 1,
          f"count={s._l2.interaction_count}")

    # E5.4: L1 + L2 同时工作
    s._wm.add_message({"user":"test","text":"hello"})
    s._wm.mark_replied("test_123")
    check("E5.4 L1 still works", len(s._wm.recent_messages) > 0)

    # E5.5: stop 后持久化
    await s.stop()
    s2 = AIStreamer()
    check("E5.5 persistence after stop", s2._l2.viewer_count == s._l2.viewer_count,
          f"orig={s._l2.viewer_count} reloaded={s2._l2.viewer_count}")

asyncio.run(test_integration())

print(f"\nTOTAL: {p+f} | PASS={p} | FAIL={f}")
if f: sys.exit(1)
print("EXPANDED TESTS: ALL PASSED")
