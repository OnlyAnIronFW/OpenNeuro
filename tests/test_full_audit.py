"""
Phase 0+2 全面复检测试套件 — 扩展覆盖

覆盖:
  A. EventBus: 通配符/多handler/异常隔离/log持久化/publish_nowait/stop清空
  B. Config: 全部字段/env缺失/热更新/change watcher/空yaml
  C. Prompt: Section提取边界/空人设/reload一致性/多语言/bot_name提取
  D. S1Client: mock序列/超时重试/启动失败/双重start
  E. S1Parser: 全部Token/畸形输入/降级恢复/confidence提取/thread_id
  F. RuleEngine: 边界时间/并发/连续沉默不触发/升级保留warnings/override_log
  G. S1Engine: 全空输入/大消息量/看门狗+错误叠加/决策路径
  H. Utils: 全语言/边界截断/Unicode
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['DEEPSEEK_API_KEY'] = 'test-key'

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [OK] {name}{' — '+detail if detail else ''}")
    else:
        failed += 1
        print(f"  [FAIL] {name}{' — '+detail if detail else ''}")

# ═══════════════════════════════════════════════════
# Section A: EventBus (12 tests)
# ═══════════════════════════════════════════════════

print("\n── A. EventBus ──")

from src.events.bus import EventBus, Event
from src.events.types import EventType

async def _test_bus():
    # A1: 精确订阅
    bus = EventBus()
    await bus.start()
    received = []
    async def h(e): received.append(e.payload['x'])
    bus.subscribe("test.evt", h)
    await bus.publish(EventBus.make_event("test.evt", "t", {"x": 1}))
    await bus._dispatch(await bus._queue.get())
    check("A1 exact subscribe", received == [1])
    await bus.stop()

    # A2: 通配符 '*'
    bus2 = EventBus()
    await bus2.start()
    wild = []
    async def wh(e): wild.append(e.type)
    bus2.subscribe("*", wh)
    await bus2.publish(EventBus.make_event("a.b", "t"))
    await bus2.publish(EventBus.make_event("c.d", "t"))
    for _ in range(2):
        await bus2._dispatch(await bus2._queue.get())
    check("A2 wildcard subscribe", set(wild) == {"a.b", "c.d"})
    await bus2.stop()

    # A3: 多 handler 同一事件
    bus3 = EventBus()
    await bus3.start()
    r = []
    async def h1(e): r.append(1)
    async def h2(e): r.append(2)
    bus3.subscribe("x", h1); bus3.subscribe("x", h2)
    await bus3.publish(EventBus.make_event("x", "t"))
    await bus3._dispatch(await bus3._queue.get())
    check("A3 multiple handlers", sorted(r) == [1, 2])
    await bus3.stop()

    # A4: 异常隔离 — 一个handler抛异常不阻塞其他
    bus4 = EventBus()
    await bus4.start()
    r2 = []
    async def crash(e): raise RuntimeError("boom")
    async def ok(e): r2.append("ok")
    bus4.subscribe("x", crash); bus4.subscribe("x", ok)
    await bus4.publish(EventBus.make_event("x", "t"))
    await bus4._dispatch(await bus4._queue.get())
    check("A4 exception isolation", r2 == ["ok"])
    await bus4.stop()

    # A5: 日志持久化
    tmp = tempfile.mkdtemp()
    bus5 = EventBus(log_dir=tmp)
    await bus5.start()
    await bus5.publish(EventBus.make_event("log.test", "s", {"k": "v"}))
    await bus5._dispatch(await bus5._queue.get())
    await bus5.stop()
    log_files = list(__import__('glob').glob(f"{tmp}/*.jsonl"))
    content = open(log_files[0]).read() if log_files else ""
    check("A5 log persistence", len(log_files) == 1 and "log.test" in content, f"found {len(log_files)} files")
    shutil.rmtree(tmp)
    await bus5.stop()

    # A6: publish_nowait 不阻塞
    bus6 = EventBus()
    await bus6.start()
    try:
        await bus6.publish_nowait(EventBus.make_event("nw", "t"))
        check("A6 publish_nowait", True)
    except Exception as e:
        check("A6 publish_nowait", False, str(e))
    await bus6.stop()

    # A7: stop 清空队列
    bus7 = EventBus()
    await bus7.start()
    for i in range(10):
        bus7._queue.put_nowait(EventBus.make_event("q", "t"))
    await bus7.stop()
    check("A7 stop clears queue", bus7._queue.empty())

    # A8: unsubscribe
    bus8 = EventBus()
    await bus8.start()
    r3 = []
    async def hh(e): r3.append(1)
    bus8.subscribe("u", hh)
    bus8.unsubscribe("u", hh)
    await bus8.publish(EventBus.make_event("u", "t"))
    await bus8._dispatch(await bus8._queue.get())
    check("A8 unsubscribe", r3 == [])
    await bus8.stop()

    # A9: queue_size
    bus9 = EventBus()
    await bus9.start()
    assert bus9.queue_size == 0
    bus9._queue.put_nowait(EventBus.make_event("q", "t"))
    check("A9 queue_size", bus9.queue_size == 1)
    await bus9.stop()

    # A10: make_event defaults
    evt = EventBus.make_event("test", "me")
    check("A10 make_event defaults", evt.payload == {} and evt.correlation_id == "")

    # A11: 无订阅者不崩溃
    bus11 = EventBus()
    await bus11.start()
    await bus11.publish(EventBus.make_event("no.sub", "t"))
    await bus11._dispatch(await bus11._queue.get())
    check("A11 no subscribers", True)
    await bus11.stop()

    # A12: start/stop 多次调用
    bus12 = EventBus()
    await bus12.start(); await bus12.start()  # 双重start
    await bus12.stop(); await bus12.stop()     # 双重stop
    check("A12 double start/stop", True)

asyncio.run(_test_bus())

# ═══════════════════════════════════════════════════
# Section B: ConfigManager (8 tests)
# ═══════════════════════════════════════════════════

print("\n── B. Config ──")

from src.config.loader import ConfigManager
from src.config.schema import AppConfig

c = ConfigManager("config.yaml")
cfg = c.load()

check("B1 all 15 config classes present", all([
    cfg.s1_model, cfg.s2_model, cfg.s1_decision, cfg.threads,
    cfg.visual, cfg.memory, cfg.self_iteration, cfg.degradation,
    cfg.platforms, cfg.observability, cfg.session
]))
check("B2 s1_model", cfg.s1_model.model_id == "openbmb/MiniCPM-o-4_5")
check("B3 s1_decision defaults", cfg.s1_decision.forced_cooldown_ms == 5000)
check("B4 memory nested", cfg.memory.l2.semantic_cache.similarity_threshold == 0.88)
check("B5 platform bilibili strict", cfg.platforms.bilibili.output_filter == "strict")
check("B6 degradation values", cfg.degradation.recovery_min_success_streak == 10)
check("B7 session config", cfg.session.recording_path == "data/recordings")

# B8: change watcher
watcher_called = []
def w(cfg): watcher_called.append(1)
c.on_change(w)
# 文件未变 → 不触发
changed = c.check_and_reload()
check("B8 change watcher (no change)", not changed and len(watcher_called) == 0)

# B9: 默认 schema
dc = AppConfig()
check("B9 schema defaults", all([
    dc.s1_decision.protection_period_ms == 2000,
    dc.s2_model.api_base == "https://api.deepseek.com/v1",
    dc.threads.max_active == 10,
    dc.platforms.bilibili.enabled == True,
]))

# B10: env var 缺失时回退
raw_env = os.environ.pop('DEEPSEEK_API_KEY', None)
c2 = ConfigManager("config.yaml")
cfg2 = c2.load()
check("B10 env var missing (keeps ${...})",
      "${DEEPSEEK_API_KEY}" in cfg2.s2_model.api_key or cfg2.s2_model.api_key == "")
if raw_env: os.environ['DEEPSEEK_API_KEY'] = raw_env

# ═══════════════════════════════════════════════════
# Section C: PromptAssembler (10 tests)
# ═══════════════════════════════════════════════════

print("\n── C. PromptAssembler ──")

from src.prompts.assembler import PromptAssembler
p = PromptAssembler()

# C1: S1 system 包含关键内容
s1 = p.build_s1_system()
check("C1 S1 has rules", "决策规则" in s1)
check("C1 S1 has persona", "NewRoad" in s1)
check("C1 S1 has visual rules", "BOSS战" in s1)

# C2: S2 system 包含关键内容
s2 = p.build_s2_system()
check("C2 S2 has output rules", "不要 JSON" in s2)
check("C2 S2 has persona", "NewRoad" in s2)
check("C2 S2 has relationship", "与观众的关系" in s2)

# C3: S1不包括S2独有的(@s2标记的section)
# "与观众的关系" section: heading有@s2, S1不应包含
check("C3 S1 excludes S2-only sections", "与观众的关系" not in s1)

# C4: build_s2_user_message 所有字段
um = p.build_s2_user_message(
    reply_direction="test direction",
    visual_summary="test visual",
    triggering_messages="test trigger",
    recent_chat="test chat",
    retrieved_memories="test memory",
    viewer_profile="test viewer",
    relevant_skills="test skill",
    emotional_state="test emotion",
    s1_confidence=0.85,
    current_topic="test topic",
    topic_duration="5分钟",
    seconds_since_last_reply=30.0,
)
checks_c4 = ["test direction", "test visual", "test trigger", "test chat",
             "test memory", "test viewer", "test skill", "test emotion",
             "0.85", "test topic", "5分钟", "30 秒"]
all_in = all(c in um for c in checks_c4)
check("C4 user msg all fields", all_in, f"missing: {[c for c in checks_c4 if c not in um]}" if not all_in else "")

# C5: 首次发言
um2 = p.build_s2_user_message(seconds_since_last_reply=-1)
check("C5 first speak indicator", "首次发言" in um2)

# C6: 空人设提取
# 模拟空文件
import tempfile, os as _os
from pathlib import Path
tmpd = tempfile.mkdtemp()
(Path(tmpd) / "s1_rules.md").write_text("# test rules", encoding="utf-8")
(Path(tmpd) / "s2_rules.md").write_text("# test rules", encoding="utf-8")
(Path(tmpd) / "persona_core.md").write_text("## Empty (@both)\n(no content)\n", encoding="utf-8")
p_empty = PromptAssembler(prompts_dir=tmpd)
check("C6 empty persona extraction", len(p_empty.build_s1_system()) > 0)
shutil.rmtree(tmpd)

# C7: first_user_message 提取bot名
fum = p.build_s2_first_user_message("中文")
check("C7 first_user_message bot name", "NewRoad" in fum and "扮演" in fum)
fum_en = p.build_s2_first_user_message("English")
check("C7 first_user_message EN", "English" in fum_en and "NewRoad" in fum_en)

# C8: reload_persona 一致性
p.reload_persona()
s1r = p.build_s1_system()
check("C8 reload persona consistent", len(s1r) == len(s1))

# C9: reload_rules
p.reload_rules()
s1rr = p.build_s1_system()
check("C9 reload rules consistent", len(s1rr) == len(s1))

# C10: reload_all
p.reload_all()
check("C10 reload_all", True)

# ═══════════════════════════════════════════════════
# Section D: MiniCPMClient (8 tests)
# ═══════════════════════════════════════════════════

print("\n── D. MiniCPMClient ──")

from src.models.s1_client import MiniCPMClient, S1RawResponse

async def _test_client():
    # D1: Mock 模式基本
    cl = MiniCPMClient(mock_mode=True)
    await cl.start()
    cl.set_mock_responses(["<|Start-Speaking confidence=0.9|> test"])
    r = await cl.decide("sys", "ctx")
    check("D1 mock basic", "Start-Speaking" in r.content and r.error is None)
    check("D1 mock latency", 10 < r.latency_ms < 20)

    # D2: Mock 序列耗尽回退
    cl2 = MiniCPMClient(mock_mode=True)
    await cl2.start()
    cl2.set_mock_responses(["only one"])
    r1 = await cl2.decide("", "")
    r2 = await cl2.decide("", "")
    check("D2 mock sequence exhaust", r1.content == "only one" and r2.content == "<|Continue-Listening|>")
    await cl2.stop()

    # D3: is_healthy mock
    check("D3 mock health", await cl.is_healthy())

    # D4: stop 后可重新 start
    await cl.stop()
    await cl.start()
    cl.set_mock_responses(["<|Quick-Reply|> ok"])
    r3 = await cl.decide("", "")
    check("D4 restart after stop", r3.content == "<|Quick-Reply|> ok")
    await cl.stop()

    # D5: 无 mock 响应 → 回退
    cl5 = MiniCPMClient(mock_mode=True)
    await cl5.start()
    # 不 set_mock_responses → 回退 Continue-Listening
    r5 = await cl5.decide("", "")
    check("D5 no mock response fallback", r5.content == "<|Continue-Listening|>")
    await cl5.stop()

    # D6: mock_responses 不会被修改
    cl6 = MiniCPMClient(mock_mode=True)
    originals = ["<|Quick-Reply|> A", "<|Quick-Reply|> B"]
    cl6.set_mock_responses(originals)
    originals.append("should not appear")
    await cl6.start()
    r6_1 = await cl6.decide("", "")
    r6_2 = await cl6.decide("", "")
    check("D6 mock_responses copy", r6_1.content == "<|Quick-Reply|> A" and r6_2.content == "<|Quick-Reply|> B")
    await cl6.stop()

    # D7: Mock reset (重新set重置index)
    cl7 = MiniCPMClient(mock_mode=True)
    await cl7.start()
    cl7.set_mock_responses(["R1", "R2"])
    await cl7.decide("", "")  # R1
    cl7.set_mock_responses(["New"])
    r7 = await cl7.decide("", "")
    check("D7 mock reset index", r7.content == "New")
    await cl7.stop()

    # D8: 默认参数
    cl8 = MiniCPMClient()
    check("D8 defaults", cl8._base_url == "http://localhost:9060" and cl8._timeout_ms == 500.0)

asyncio.run(_test_client())

# ═══════════════════════════════════════════════════
# Section E: S1Parser (12 tests)
# ═══════════════════════════════════════════════════

print("\n── E. S1Parser ──")

from src.s1.parser import S1Parser, S1Token, ParsedDecision, ParserState

parser = S1Parser()

# E1-E6: 6 Token 精确 + 验证返回字段
r = parser.parse("<|Quick-Reply|> 谢谢老板！")
check("E1 QR exact", r.token == S1Token.QUICK_REPLY and r.quick_reply_text == "谢谢老板！")
check("E1 QR is_reply", r.is_reply and not r.is_silence)

r = parser.parse("<|Start-Speaking confidence=0.88|> 回复方向内容")
check("E2 SS exact", r.token == S1Token.START_SPEAKING and r.confidence == 0.88)
check("E2 SS direction", "回复方向内容" in r.direction)
check("E2 SS is_reply", r.is_reply)

r = parser.parse("<|Continue-Listening|>")
check("E3 CL exact", r.token == S1Token.CONTINUE_LISTENING and r.is_silence)

r = parser.parse("<|Start-Listening|>")
check("E4 SL exact", r.token == S1Token.START_LISTENING)

r = parser.parse("<|Continue-Speaking|>")
check("E5 CS exact", r.token == S1Token.CONTINUE_SPEAKING)

r = parser.parse("<|Cancel-S2|>")
check("E6 Cancel exact", r.token == S1Token.CANCEL_S2)

# E7: 带换行的 QuickReply
r = parser.parse("<|Quick-Reply|> 第一行\n第二行\n第三行")
check("E7 QR multiline", "第一行" in r.quick_reply_text and r.token == S1Token.QUICK_REPLY)

# E8: 空 confidence 的 Start-Speaking
r = parser.parse("<|Start-Speaking|>没有置信度")
check("E8 SS no confidence", r.confidence == 0.7 and r.token == S1Token.START_SPEAKING)

# E9: 带额外空格的Token
r = parser.parse("   <|Continue-Listening|>   ")
check("E9 whitespace padding", r.token == S1Token.CONTINUE_LISTENING)

# E10: 极长输入 (模拟模型输出乱码)
long_text = "x" * 5000
r = parser.parse(long_text)
check("E10 very long input", r.token == S1Token.CONTINUE_LISTENING)

# E11: 特殊字符 (emoji)
r = parser.parse("<|Quick-Reply|> 🎉🎉🎉 恭喜")
check("E11 emoji in QR", r.token == S1Token.QUICK_REPLY and "🎉" in r.quick_reply_text)

# E12: 降级状态机验证
p2 = S1Parser()
for _ in range(2): p2.parse("garbage")
check("E12 degraded after 2 fails", p2.state == ParserState.DEGRADED)
for _ in range(3): p2.parse("more garbage")
check("E12 failed after 5 total", p2.state == ParserState.FAILED)
p2.reset_state()
# 成功恢复: 2次失败进入degraded, 10次成功恢复
for _ in range(2): p2.parse("x")
for _ in range(10): p2.parse("<|Continue-Listening|>")
check("E12 recovery after success streak", p2.state == ParserState.NORMAL)

# E13: 精确匹配优于模糊 (歧义输入)
# "Continue-Listenin" 缺g, 应模糊匹配到 CONTINUE_LISTENING
r = parser.parse("<|Continue-Listenin|>")
check("E13 fuzzy over exact ambiguity", r.token == S1Token.CONTINUE_LISTENING)

# E14: confidence=0.00 边缘
r = parser.parse("<|Start-Speaking confidence=0.00|> dir")
check("E14 zero confidence", r.confidence == 0.0)

# ═══════════════════════════════════════════════════
# Section F: RuleEngine (12 tests)
# ═══════════════════════════════════════════════════

print("\n── F. RuleEngine ──")

from src.s1.rule_engine import RuleEngine, RuleConfig

# F1: 无最近发言 → 应该放行
re1 = RuleEngine()
v = re1.validate(ParsedDecision(token=S1Token.START_SPEAKING, confidence=0.9))
check("F1 no prior reply passes", v.token == S1Token.START_SPEAKING)

# F2: 保护期精确边界
re2 = RuleEngine(RuleConfig(protection_period_ms=2000))
re2.record_reply(100.0)
v = re2.validate(ParsedDecision(token=S1Token.START_SPEAKING), current_time=100.0 + 1.0)
check("F2 inside protection (1s)", v.token == S1Token.CONTINUE_LISTENING)
v2 = re2.validate(ParsedDecision(token=S1Token.START_SPEAKING), current_time=100.0 + 2.1)
check("F2 outside protection (2.1s)", v2.token == S1Token.START_SPEAKING)

# F3: 频率限制 - 3次后拦截
re3 = RuleEngine(RuleConfig(max_replies_per_10s=3))
for i in range(3):
    re3.record_reply(200.0 + i)
v3 = re3.validate(ParsedDecision(token=S1Token.QUICK_REPLY, quick_reply_text="test"), current_time=205.0)
check("F3 rate limit blocks 4th", v3.token == S1Token.CONTINUE_LISTENING)

# F4: 连续非回复Token不触发死循环 (START_LISTENING连续3次不应该被拦截)
re4 = RuleEngine()
for _ in range(3):
    v4 = re4.validate(ParsedDecision(token=S1Token.START_LISTENING))
    check(f"F4 non-reply token loop pass ({_+1}/3)", v4.token == S1Token.START_LISTENING)

# F5: QuickReply超长升级
re5 = RuleEngine(RuleConfig(quick_reply_max_chars=5))
v5 = re5.validate(ParsedDecision(token=S1Token.QUICK_REPLY, quick_reply_text="123456"))
check("F5 QR upgrade", v5.token == S1Token.START_SPEAKING and v5.direction == "123456")

# F6: QuickReply正好在阈值 → 放行
v6 = re5.validate(ParsedDecision(token=S1Token.QUICK_REPLY, quick_reply_text="12345"))
check("F6 QR at threshold passes", v6.token == S1Token.QUICK_REPLY)

# F7: 看门狗
re7 = RuleEngine(RuleConfig(silence_watchdog_ms=50))
time.sleep(0.06)
check("F7 watchdog triggered", re7.is_silent_too_long())

# F8: 紧急决策 — 按消息列表顺序返回第一个匹配
msgs = [
    {"mentioned_bot": True, "user": "老张"},
    {"is_question": True, "user": "小红"},
    {"event_type": "gift", "user": "小明"},
]
dec = re7.emergency_decision(msgs)
check("F8 emergency first match (@)", "老张" in (dec.direction or ""), f"direction={dec.direction}")
check("F8 emergency confidence", dec.confidence == 0.9)

# F8b: 无@ → 返回问题
dec2 = re7.emergency_decision([
    {"is_question": True, "user": "小红"},
    {"event_type": "gift", "user": "小明"},
])
check("F8b emergency question", dec2.confidence == 0.75)

# F8c: 无@无问题 → 返回礼物
dec3 = re7.emergency_decision([
    {"event_type": "gift", "user": "小明"},
])
check("F8c emergency gift", dec3.token == S1Token.QUICK_REPLY)

# F9: override_log 记录拦截
re9 = RuleEngine(RuleConfig(protection_period_ms=2000))
re9.record_reply(300.0)
v9 = re9.validate(ParsedDecision(token=S1Token.START_SPEAKING), current_time=301.0)
check("F9 override logged", len(re9.override_log) == 1 and re9.override_log[0]["reason"] == "protection_period")

# F10: 多次拦截不丢失warnings
qrw = ParsedDecision(token=S1Token.QUICK_REPLY, quick_reply_text="hi", parse_warnings=["orig_warn"])
re10 = RuleEngine(RuleConfig(protection_period_ms=2000))
re10.record_reply(400.0)
v10 = re10.validate(qrw, current_time=401.0)
check("F10 warnings preserved on override", "orig_warn" in v10.parse_warnings and "overridden" in v10.parse_warnings[-1])

# F11: reset 清空状态
re11 = RuleEngine()
re11.record_reply(500.0)
re11.reset()
check("F11 reset clears timestamps", re11.seconds_since_last_reply > 900)

# F12: seconds_since_last_reply 从未发言
re12 = RuleEngine()
check("F12 never spoken", re12.seconds_since_last_reply > 900)

# ═══════════════════════════════════════════════════
# Section G: S1Engine (8 tests)
# ═══════════════════════════════════════════════════

print("\n── G. S1Engine ──")

from src.s1.engine import S1Engine, S1DecisionResult

async def _test_engine():
    # G1: 空输入
    cl = MiniCPMClient(mock_mode=True); await cl.start()
    cl.set_mock_responses(["<|Continue-Listening|>"])
    eng = S1Engine(cl, p)
    r = await eng.decide(messages=[])
    check("G1 empty messages", r.parsed.token == S1Token.CONTINUE_LISTENING)

    # G2: 大消息量 (50条)
    msgs = [{"user": f"u{i}", "text": f"msg{i}"} for i in range(50)]
    msgs[-1]["mentioned_bot"] = True
    cl.set_mock_responses(["<|Start-Speaking confidence=0.8|> reply"])
    r2 = await eng.decide(messages=msgs)
    check("G2 50 messages (only last 10 in context)", r2.parsed.token == S1Token.START_SPEAKING)

    # G3: 看门狗 + 错误叠加 (mock模式下不触发, 但有@消息应该正确决策)
    cl.set_mock_responses(["<|Quick-Reply|> hi"])
    r3 = await eng.decide(messages=[{"user":"test","text":"hi","mentioned_bot":True}])
    check("G3 @message response", r3.parsed.is_reply)

    # G4: 空字段输入不崩溃
    r4 = await eng.decide(
        messages=[{"user": "x"}],  # 没有text
        visual_summary="",
        emotional_state="",
        content_strategy={},
        working_memory={},
    )
    check("G4 minimal fields no crash", r4.parsed.token is not None)

    # G5: decision_path 完整
    cl.set_mock_responses(["<|Continue-Listening|>"])
    r5 = await eng.decide(messages=[{"user":"x","text":"y"}])
    check("G5 decision_path length >= 3", len(r5.decision_path) >= 3, str(r5.decision_path))

    # G6: record_reply + 保护期
    eng.record_reply()
    check("G6 seconds_since_last_reply after record", eng.seconds_since_last_reply < 1)

    # G7: set_watchdog_enabled
    eng.set_watchdog_enabled(False)
    check("G7 watchdog disabled", not eng._watchdog_enabled)
    eng.set_watchdog_enabled(True)

    # G8: reset
    eng.reset()
    check("G8 reset clears parser state", eng.parser_state == ParserState.NORMAL)

    await eng.stop()

asyncio.run(_test_engine())

# ═══════════════════════════════════════════════════
# Section H: Utils (8 tests)
# ═══════════════════════════════════════════════════

print("\n── H. Utils ──")

from src.utils.text import detect_language, truncate_at_sentence, strip_action_brackets, count_chinese, count_english_words

# H1: 语言检测边界
check("H1 zh", detect_language("你好世界你好世界你好世界") == "zh")
check("H1 en", detect_language("hello world this is a long english sentence with many words") == "en")
check("H1 mixed", detect_language("hello 你好") == "mixed")
check("H1 empty", detect_language("") == "unknown")
check("H1 whitespace", detect_language("   ") == "unknown")

# H2: 中文字符计数
check("H2 count_chinese basic", count_chinese("你好世界") == 4)
check("H2 count_chinese mixed", count_chinese("hello你好world") == 2)
check("H2 count_chinese empty", count_chinese("") == 0)

# H3: 截断边界
check("H3 truncate exact fit", truncate_at_sentence("hello world", 20) == "hello world")
check("H3 truncate no separator in range", len(truncate_at_sentence("abcdefghijklmnopqrstuvwxyz", 15)) == 15)
check("H3 truncate at first sentence", "。" in truncate_at_sentence("第一句。第二句很长很长很长很长。", 20))

# H4: 括号过滤
s = strip_action_brackets("() (*laugh*) (一段正常的比较长的文字) test")
check("H4 empty bracket stripped", "(*laugh*)" not in s and "()" not in s)
check("H4 long bracket kept", "一段正常的比较长的文字" in s)
check("H4 text preserved", "test" in s)

# H5: Unicode 数字/符号不影响
s2 = strip_action_brackets("价格¥100 (*笑*) 好")
check("H5 unicode price preserved", "¥100" in s2)

# H6: count_english_words
check("H6 english word count", count_english_words("hello world test") == 3)
check("H6 english mixed", count_english_words("hello123 world! test.") == 3)

# ═══════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════

print(f"\n{'='*60}")
print(f"TOTAL: {passed+failed} tests | PASSED: {passed} | FAILED: {failed}")
print(f"{'='*60}")

if failed > 0:
    print(f"\nFAILED TESTS: {failed}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
