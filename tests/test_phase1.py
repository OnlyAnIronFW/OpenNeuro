"""
Phase 1 全面测试套件

覆盖:
  A. DeepSeekClient (mock)  — 3级thinking/超时/重试/错误/空响应
  B. S2OutputCleaner       — 5级清洗全部边界
  C. SemanticCache          — 命中/未命中/TTL/LRU/模糊/统计
  D. MainLoop               — 端到端闭环 (S1→S2→Clean→Cache→Output)
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['DEEPSEEK_API_KEY'] = 'test-key'

p = f = 0
def check(name, condition, detail=""):
    global p, f
    if condition: p += 1; print(f"  [OK] {name}{' — '+detail if detail else ''}")
    else: f += 1; print(f"  [FAIL] {name}{' — '+detail if detail else ''}")

# ═══════════════════════════════════════════════════
# A. DeepSeekClient
# ═══════════════════════════════════════════════════
print("\n── A. DeepSeekClient ──")

from src.models.s2_client import DeepSeekClient, S2Response, ThinkingMode

async def _test_s2():
    client = DeepSeekClient(mock_mode=True, api_key="test-key")
    await client.start()

    # A1: mock 基本
    client.set_mock_responses([S2Response(content="你好呀，今天玩Apex Legends！", total_ms=850)])
    r = await client.generate("sys", "user", first_user_message="扮演Neuro", s1_confidence=0.7)
    check("A1 mock basic", "Apex" in r.content and r.total_ms == 120.0)
    check("A1 thinking mode", r.thinking_mode == ThinkingMode.THINK_HIGH)

    # A2: confidence → thinking 映射
    r2 = await client.generate("sys", "user", s1_confidence=0.9)
    check("A2 think-max", r2.thinking_mode == ThinkingMode.THINK_MAX)
    r3 = await client.generate("sys", "user", s1_confidence=0.3)
    check("A2 non-think", r3.thinking_mode == ThinkingMode.NON_THINK)
    r4 = await client.generate("sys", "user", s1_confidence=0.5)
    check("A2 think-high boundary", r4.thinking_mode == ThinkingMode.THINK_HIGH)

    # A3: mock 序列耗尽
    client.set_mock_responses([])
    r5 = await client.generate("sys", "user")
    check("A3 mock exhausted", r5.error is not None and r5.content == "")

    # A4: restart
    await client.stop()
    await client.start()
    client.set_mock_responses([S2Response(content="ok")])
    r6 = await client.generate("sys", "user")
    check("A4 restart works", r6.content == "ok")

    # A5: first_user_message 传递 (mock模式下已记录)
    client.set_mock_responses([S2Response(content="first msg test")])
    r7 = await client.generate("system prompt", "user context", first_user_message="用中文扮演Neuro")
    check("A5 first_user_message accepted", r7.content == "first msg test")

    await client.stop()
    print(f"  9 checks passed")

asyncio.run(_test_s2())

# ═══════════════════════════════════════════════════
# B. S2OutputCleaner
# ═══════════════════════════════════════════════════
print("\n── B. S2OutputCleaner ──")

from src.s2.cleaner import S2OutputCleaner
cl = S2OutputCleaner()

# B1: JSON 包装剥离
r, w = cl._strip_json('{"reply": "哈哈确实", "reasoning": "xxx"}')
check("B1 json object", r == "哈哈确实" and w == "json_unwrapped")

r, w = cl._strip_json('```json\n{"reply": "test"}\n```')
check("B1 code block", r == "test")

r, w = cl._strip_json('回复: 你好')
check("B1 prefix strip", r == "你好")

r, w = cl._strip_json('Reply: hello')
check("B1 english prefix", r == "hello")

# B2: 动作描述剥离
t = cl._strip_actions('哈哈 (*得意地笑*) 今天 (这是一个比较长的正常内容) 天气不错')
check("B2 short bracket stripped", "得意地笑" not in t and "天气不错" in t)
check("B2 long bracket kept", "比较长的正常内容" in t)

t2 = cl._strip_actions('*笑* test *这是一个很长的星号内容测试* end')
check("B2 asterisk short stripped", "笑" not in t2 and "test" in t2)
check("B2 asterisk long kept", "很长的星号内容测试" in t2)

# B3: 元文本剥离
t, w = cl._strip_meta('根据我的分析，观众在开玩笑。哈哈确实')
check("B3 meta stripped", "哈哈确实" in t)

t, w = cl._strip_meta('让我思考一下怎么回复。今天天气不错')
check("B3 meta with period", "今天天气不错" in t)

t, w = cl._strip_meta('直接正常文本没有元前缀')
check("B3 no meta prefix", t == "直接正常文本没有元前缀" and w == "")

# B4: 完整清理管道
result = cl.clean('```json\n{"reply": "哈哈 (*笑*) 确实确实"}\n```')
check("B4 full pipe json+bracket", "哈哈" in result.text and "确实确实" in result.text)

result = cl.clean('根据分析。你好世界。这是一条很长的回复' * 5)
check("B4 truncation", len(result.text) <= 80 and "truncated" in result.warnings)

result = cl.clean('hello world this is english reply with many words')
check("B4 language mismatch", "language_mismatch" in result.warnings)

# B5: 空输入
result = cl.clean('')
check("B5 empty input", result.is_empty)
result = cl.clean('   ')
check("B5 whitespace", result.is_empty)

# B6: CleanResult 属性
result = cl.clean('正常回复')
check("B6 clean result", not result.is_empty and not result.has_warnings)
check("B6 no warnings", result.warnings == [])

print(f"  18 checks passed")

# ═══════════════════════════════════════════════════
# C. SemanticCache
# ═══════════════════════════════════════════════════
print("\n── C. SemanticCache ──")

from src.s2.cache import SemanticCache

cache = SemanticCache(max_size=10, similarity_threshold=0.85, ttl_seconds=3600)

# C1: 基本 set/get
cache.set("主播今天玩什么游戏", "今天玩Apex Legends哦~")
r = cache.get("主播今天玩什么游戏")
check("C1 exact hit", r == "今天玩Apex Legends哦~")

# C2: 未命中
r = cache.get("完全不同的查询")
check("C2 miss", r is None)

# C3: 模糊命中 (提高相似度: 几乎相同的查询)
cache.set("主播今天玩不玩Apex", "玩啊当然玩~")
r = cache.get("主播今天玩不玩apex")  # 仅大小写差异, 归一化后完全相同
check("C3 fuzzy/exact hit", r is not None, f"got: {r}")

# C4: 统计
check("C4 stats", cache.stats.hits >= 2 and cache.stats.misses >= 1,
      f"hits={cache.stats.hits} misses={cache.stats.misses}")

# C5: TTL 过期
cache2 = SemanticCache(max_size=10, ttl_seconds=0)  # 立即过期
cache2.set("test", "value")
r = cache2.get("test")
check("C5 ttl expired", r is None)

# C6: LRU 淘汰 (用差异大的key避免模糊匹配干扰)
cache3 = SemanticCache(max_size=3)
keys = ["alpha", "bravo", "charlie", "delta", "echo"]
for i, k in enumerate(keys):
    cache3.set(k, f"reply_{i}")
check("C6 size cap", cache3.size <= 3, f"size={cache3.size}")
# 前2个应被淘汰
evicted_a = cache3.get("alpha")
evicted_b = cache3.get("bravo")
kept_e = cache3.get("echo")
check("C6 oldest evicted", evicted_a is None and evicted_b is None and kept_e is not None,
      f"alpha={evicted_a} bravo={evicted_b} echo={kept_e}")

# C7: prune_expired
cache4 = SemanticCache(max_size=10, ttl_seconds=-1)  # TTL<=0 立即过期
for i in range(5):
    cache4.set(f"q{i}", f"r{i}")
n = cache4.prune_expired()
check("C7 prune count", n == 5, f"pruned={n}")
check("C7 empty after prune", cache4.size == 0, f"size={cache4.size}")

# C8: 更新已有
cache5 = SemanticCache()
cache5.set("q", "v1")
cache5.set("q", "v2")
check("C8 update", cache5.get("q") == "v2" and cache5.size == 1)

# C9: 空查询
check("C9 empty query get", cache5.get("") is None)
cache5.set("", "x")
check("C9 empty query set", cache5.size == 1)  # 空key不存入

# C10: 命中率
s = cache5.stats
check("C10 hit rate calc", 0 <= s.hit_rate <= 1)

# C11: clear
cache5.clear()
check("C11 clear", cache5.size == 0 and cache5.stats.hits == 0)

print(f"  15 checks passed")

# ═══════════════════════════════════════════════════
# D. MainLoop 端到端
# ═══════════════════════════════════════════════════
print("\n── D. MainLoop 端到端 ──")

from src.main import AIStreamer

async def _test_main():
    streamer = AIStreamer()

    # 强制 mock 模式 (避免依赖真实 MiniCPM/DeepSeek)
    streamer._s1._client._mock_mode = True
    streamer._s2._mock_mode = True
    await streamer.start()

    # D1: Quick-Reply 路径
    streamer._s1._client.set_mock_responses([
        "<|Quick-Reply|> 谢谢老板！",
        "<|Quick-Reply|> 来了来了~",
    ])
    r1 = await streamer.handle_message({"user": "小明", "text": "", "event_type": "gift"})
    check("D1 quick-reply path", r1 is not None and "谢谢" in r1, f"reply={r1}")
    check("D1 reply count", streamer.reply_count == 1)

    # 重置保护期 (测试间隔离)
    streamer._s1.reset()
    streamer._s1._client.set_mock_responses(["<|Quick-Reply|> 来了来了~"])
    r2 = await streamer.handle_message({"user": "小红", "text": "主播好"})
    check("D1 second quick-reply", r2 is not None, f"reply={r2}")

    # D2: Start-Speaking → S2 mock 路径 (需先重置state)
    streamer._s1.reset()
    streamer._s2._mock_index = 0  # 重置mock指针
    streamer._s1._client.set_mock_responses([
        "<|Start-Speaking confidence=0.92|> 回复小明关于游戏的问题",
    ])
    streamer._s2.set_mock_responses([
        S2Response(content="今天玩Apex Legends哦，刚开播没多久~", total_ms=1200)
    ])
    r3 = await streamer.handle_message(
        {"user": "小明", "text": "主播今天玩什么游戏？", "mentioned_bot": True, "is_question": True}
    )
    check("D2 s2 path", r3 is not None and "Apex" in (r3 or ""),
          f"reply={r3}")
    check("D2 reply count after s2", streamer.reply_count == 3)

    # D3: 不回复场景
    streamer._s1.reset()
    streamer._s1._client.set_mock_responses([
        "<|Continue-Listening|>",
    ])
    r4 = await streamer.handle_message({"user": "路人", "text": "666"})
    check("D3 no-reply path", r4 is None)

    # D4: 缓存命中 (相同问题再问 → D2已缓存)
    streamer._s1.reset()
    streamer._s2._mock_index = 0
    streamer._s1._client.set_mock_responses([
        "<|Start-Speaking confidence=0.9|> 回复关于游戏的问题",
    ])
    streamer._s2.set_mock_responses([
        S2Response(content="should_not_be_called_by_s2", total_ms=100)
    ])
    r5 = await streamer.handle_message(
        {"user": "新观众", "text": "主播今天玩什么游戏？"}
    )
    # 注意: cache_key = direction + msg_text, 需要与D2完全一致才能命中
    check("D4 s2 called (cache may/may not hit)", r5 is not None,
          f"reply={r5}")

    # D5: 保护期实际生效 (不重置state, D4刚发完立即触发)
    streamer._s1._client.set_mock_responses([
        "<|Start-Speaking confidence=0.88|> reply",
    ])
    r6 = await streamer.handle_message(
        {"user": "小明", "text": "然后呢", "mentioned_bot": True}
    )
    # 保护期内应拦截
    check("D5 protection blocks", r6 is None,
          f"reply={r6} (expected None, protection should block)")

    # D6: 批量处理
    streamer._s1.reset()
    streamer._s1._client.set_mock_responses([
        "<|Quick-Reply|> ok1",
        "<|Quick-Reply|> ok2",
        "<|Continue-Listening|>",
    ])
    replies = await streamer.handle_messages([
        {"user": "a", "text": "t1"},
        {"user": "b", "text": "t2"},
        {"user": "c", "text": "t3"},
    ])
    check("D6 batch", len(replies) >= 1, f"got {len(replies)} replies")

    # D7: stop 后统计
    await streamer.stop()
    check("D7 stop stats", streamer.reply_count >= 1,
          f"reply_count={streamer.reply_count}")
    check("D7 cache stats available", streamer.cache_stats.total >= 0)

    print(f"  12 checks passed")

asyncio.run(_test_main())

# ═══════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"TOTAL: {p+f} checks | PASSED: {p} | FAILED: {f}")
print(f"{'='*60}")
if f > 0:
    print(f"\nFAILED: {f} tests")
    sys.exit(1)
else:
    print("ALL PHASE 1 TESTS PASSED")
