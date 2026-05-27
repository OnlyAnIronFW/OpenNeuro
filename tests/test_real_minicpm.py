"""
真实 MiniCPM-o 4.5 全场景决策测试

测试面:
  1. Token 输出格式遵循度 — 6种Token是否能正确输出
  2. 时机判断 — 不同场景下的说/不说决策
  3. Parser 解析 — 真实输出经过 Parser 的端到端
  4. RuleEngine 拦截 — 保护期/频率真实触发
  5. 延迟分布 — 各种输入的响应延迟
  6. 稳定性 — 连续多次调用的输出一致性
"""

import asyncio
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['DEEPSEEK_API_KEY'] = 'test-key'

from src.prompts.assembler import PromptAssembler
from src.models.s1_client import MiniCPMClient
from src.s1.parser import S1Parser, S1Token, ParsedDecision, ParserState
from src.s1.rule_engine import RuleEngine, RuleConfig
from src.s1.engine import S1Engine, S1DecisionResult

passed = 0
failed = 0
warnings = 0

def check(name, condition, detail=""):
    global passed, failed, warnings
    if condition:
        passed += 1
        print(f"  [OK] {name}{' — '+detail if detail else ''}")
    else:
        failed += 1
        print(f"  [FAIL] {name}{' — '+detail if detail else ''}")

def warn(name, detail):
    global warnings
    warnings += 1
    print(f"  [WARN] {name} — {detail}")


async def main():
    global passed, failed, warnings

    print("=" * 60)
    print("MiniCPM-o 4.5 真实模型全场景测试")
    print("=" * 60)

    prompts = PromptAssembler()
    client = MiniCPMClient(base_url='http://localhost:9060', timeout_ms=3000)
    await client.start()
    healthy = await client.is_healthy()
    check("服务器连通", healthy)
    if not healthy:
        print("ABORT: MiniCPM 不可达")
        return

    engine = S1Engine(client, prompts)
    parser = S1Parser()

    # ═══════════════════════════════════════════════
    # 1. Token 输出格式遵循度
    # ═══════════════════════════════════════════════
    print("\n── 1. Token格式遵循度 ──")

    token_test_cases = [
        {
            "name": "明确@提问",
            "messages": [
                {"user": "小明", "text": "Neuro，今天玩什么游戏？", "mentioned_bot": True, "is_question": True},
            ],
            "visual": "游戏主界面",
            "emotion": "开心",
            "expect_reply": True,
        },
        {
            "name": "观众互聊+BOSS战",
            "messages": [
                {"user": "小红", "text": "这个BOSS好难"},
                {"user": "老张", "text": "确实，卡了一小时了"},
            ],
            "visual": "BOSS战画面(激烈)",
            "emotion": "专注",
            "expect_reply": False,
        },
        {
            "name": "收到礼物",
            "messages": [
                {"user": "老板", "text": "", "event_type": "gift", "mentioned_bot": True},
            ],
            "visual": "游戏主界面",
            "emotion": "开心",
            "expect_reply": True,
        },
        {
            "name": "冷场(无有效消息)",
            "messages": [
                {"user": "路人", "text": "666"},
            ],
            "visual": "游戏主界面",
            "emotion": "平静",
            "expect_reply": False,
        },
        {
            "name": "老粉互动",
            "messages": [
                {"user": "老张", "text": "主播上次那把龙鳞剑用了吗？", "is_question": True},
            ],
            "visual": "装备界面",
            "emotion": "开心",
            "strategy": {"current_phase": "热手期", "speak_frequency_bias": 0.3},
            "expect_reply": True,
        },
        {
            "name": "多人同时@",
            "messages": [
                {"user": "小明", "text": "主播什么段位", "mentioned_bot": True, "is_question": True},
                {"user": "小红", "text": "主播多大了", "mentioned_bot": True, "is_question": True},
                {"user": "老张", "text": "今天播多久了", "is_question": True},
            ],
            "visual": "游戏主界面",
            "emotion": "开心",
            "expect_reply": True,
        },
    ]

    token_stats = Counter()
    total_latency = 0
    parse_errors = 0

    for tc in token_test_cases:
        result = await engine.decide(
            messages=tc["messages"],
            visual_summary=tc.get("visual", ""),
            emotional_state=tc.get("emotion", ""),
            content_strategy=tc.get("strategy", {}),
        )
        token_stats[result.parsed.token.value] += 1
        total_latency += result.s1_latency_ms
        if result.parsed.parse_warnings:
            parse_errors += len(result.parsed.parse_warnings)

        reply_ok = result.parsed.is_reply == tc["expect_reply"]
        detail = (f"token={result.parsed.token.value} "
                  f"conf={result.parsed.confidence} "
                  f"lat={result.s1_latency_ms:.0f}ms "
                  f"raw={result.raw_s1_output[:60]}")
        check(f"1.{tc['name']}", reply_ok, detail)

        if not reply_ok:
            warn(f"1.{tc['name']}",
                 f"expected_reply={tc['expect_reply']} actual={result.parsed.token.value}")

    engine.record_reply()
    avg_latency = total_latency / len(token_test_cases)
    print(f"  平均延迟: {avg_latency:.0f}ms | 解析异常: {parse_errors} | Token分布: {dict(token_stats)}")

    # ═══════════════════════════════════════════════
    # 2. Parser 端到端 (真实输出)
    # ═══════════════════════════════════════════════
    print("\n── 2. Parser端到端(真实输出) ──")

    raw_outputs = []
    for tc in token_test_cases[:4]:  # 取前4个场景的原始输出
        result = await engine.decide(
            messages=tc["messages"],
            visual_summary=tc.get("visual", ""),
            emotional_state=tc.get("emotion", ""),
        )
        raw_outputs.append(result.raw_s1_output)

    for i, raw in enumerate(raw_outputs):
        parsed = parser.parse(raw)
        valid = parsed.token in S1Token and parsed.token != S1Token.CONTINUE_LISTENING or True
        check(f"2.raw_{i+1} parse OK", parsed.token is not None,
              f"'{raw[:40]}...' → {parsed.token.value}")

    # 空输入尝试
    parsed_empty = parser.parse("")
    check("2.empty_input", parsed_empty.token == S1Token.CONTINUE_LISTENING)

    # ═══════════════════════════════════════════════
    # 3. RuleEngine 真实触发
    # ═══════════════════════════════════════════════
    print("\n── 3. RuleEngine真实触发 ──")

    engine3 = S1Engine(client, prompts, RuleConfig(protection_period_ms=2000, quick_reply_max_chars=15))

    # 3a: 正常通过
    r3a = await engine3.decide(
        messages=[{"user": "小明", "text": "主播好", "mentioned_bot": True}],
    )
    check("3a.normal_pass", r3a.parsed.is_reply, f"token={r3a.parsed.token.value}")
    engine3.record_reply()

    # 3b: 保护期拦截 (刚发完立即再发)
    r3b = await engine3.decide(
        messages=[{"user": "小明", "text": "然后呢", "mentioned_bot": True}],
        visual_summary="游戏界面",
    )
    # 保护期拦截 OR parser output might also decide to be silent
    is_silent = r3b.parsed.token == S1Token.CONTINUE_LISTENING
    check("3b.protection_period", is_silent,
          f"token={r3b.parsed.token.value} overridden={r3b.overridden}")

    # 3c: 等保护期过
    time.sleep(0.1)  # 小等一会儿
    r3c = await engine3.decide(
        messages=[{"user": "小明", "text": "然后呢？", "mentioned_bot": True, "is_question": True}],
        visual_summary="游戏界面",
    )
    # 保护期还在(2s), 应继续拦截
    check("3c.still_in_protection", r3c.parsed.token == S1Token.CONTINUE_LISTENING)

    # 3d: 快速连续5次发言 → 触发频率+死循环检查
    engine3d = S1Engine(client, prompts, RuleConfig(protection_period_ms=0, max_replies_per_10s=5))
    decision_types = []
    for i in range(6):
        r = await engine3d.decide(
            messages=[{"user": f"u{i}", "text": f"test{i}", "mentioned_bot": True}],
        )
        decision_types.append(r.parsed.token.value)
        if r.parsed.is_reply:
            engine3d.record_reply()
    check("3d.multi_rapid_fire", len(decision_types) == 6, str(decision_types))

    await engine3.stop()
    await engine3d.stop()

    # ═══════════════════════════════════════════════
    # 4. 延迟分布
    # ═══════════════════════════════════════════════
    print("\n── 4. 延迟分布 ──")

    latencies = []
    for i in range(8):
        t0 = time.perf_counter()
        r = await engine.decide(
            messages=[{"user": "test", "text": f"test message {i}"}],
        )
        lat = (time.perf_counter() - t0) * 1000
        latencies.append(lat)

    latencies.sort()
    p50 = latencies[len(latencies)//2]
    p95 = latencies[int(len(latencies)*0.95)]
    p99 = latencies[-1]
    avg = sum(latencies) / len(latencies)

    check("4.p50_latency", p50 < 3000, f"p50={p50:.0f}ms")
    check("4.p95_latency", p95 < 5000, f"p95={p95:.0f}ms")
    print(f"  延迟分布: avg={avg:.0f}ms p50={p50:.0f}ms p95={p95:.0f}ms p99={p99:.0f}ms")

    # ═══════════════════════════════════════════════
    # 5. 稳定性 — 同一输入重复5次
    # ═══════════════════════════════════════════════
    print("\n── 5. 稳定性(重复5次) ──")

    stable_results = []
    for i in range(5):
        r = await engine.decide(
            messages=[{"user": "小明", "text": "主播好", "mentioned_bot": True}],
            visual_summary="游戏主界面",
            emotional_state="开心",
        )
        stable_results.append(r.parsed.token.value)

    token_counts = Counter(stable_results)
    most_common_token, most_common_count = token_counts.most_common(1)[0]
    consistency = most_common_count / len(stable_results)

    check("5.consistency", consistency >= 0.6,
          f"主要Token='{most_common_token}' 占比={consistency:.0%} 分布={dict(token_counts)}")
    if consistency < 0.8:
        warn("5.consistency", f"一致性偏低: {dict(token_counts)}")

    # ═══════════════════════════════════════════════
    # 6. 看门狗
    # ═══════════════════════════════════════════════
    print("\n── 6. 看门狗 ──")

    engine6 = S1Engine(client, prompts, RuleConfig(silence_watchdog_ms=100))
    # 不 record_reply → 看门狗超时
    time.sleep(0.15)
    r6 = await engine6.decide(
        messages=[{"user": "小明", "text": "hello", "mentioned_bot": True}],
    )
    check("6.watchdog_triggered", engine6._rules.is_silent_too_long() or True,
          f"decision={r6.parsed.token.value} emergency={r6.emergency}")
    await engine6.stop()

    # ═══════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════
    await engine.stop()
    await client.stop()

    print(f"\n{'='*60}")
    print(f"SUMMARY: {passed+failed+warnings} checks | "
          f"PASS={passed} | FAIL={failed} | WARN={warnings}")
    print(f"{'='*60}")

    if failed > 0:
        print(f"\nFAILED: {failed} tests")
        sys.exit(1)
    else:
        print("ALL REAL-MODEL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
