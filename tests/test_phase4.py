"""Phase 4 全模块测试"""
import os, sys, time, asyncio, json, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['DEEPSEEK_API_KEY'] = os.environ.get('DEEPSEEK_API_KEY', 'test-key')

p = f = 0
def check(name, condition, detail=""):
    global p, f
    if condition: p += 1; print(f"  [OK] {name}{' — '+str(detail) if detail else ''}")
    else: f += 1; print(f"  [FAIL] {name}{' — '+str(detail) if detail else ''}")

# ═══════════════════════════════════════════════════
# R1: 录制器
# ═══════════════════════════════════════════════════
print("\n── R1: 录制器 ──")
from src.iteration.recorder import Recorder, load_recording, extract_interactions

tmp = tempfile.mkdtemp()
rec = Recorder(tmp)

# 模拟一场直播
rec.start("test_stream_001")
rec.record_message("小明", "主播好", True, False, "msg_1")
rec.record_s1_decision("Start-Speaking", 0.88, "回复小明")
rec.record_s2_reply("来了来了", 1200, "think-high", 78)
rec.record_viewer_reaction("小明", "好好好", 500)
rec.record_message("小红", "今天播什么", False, True, "msg_2")
rec.record_s1_decision("Quick-Reply", 0.5, "")
rec.record_s2_reply("Apex", 800, "non-think", 0)
count = rec.stop()
check("R1.1 recorded", count >= 7, f"entries={count}")
check("R1.2 file created", len(list(__import__('glob').glob(f"{tmp}/*.rec"))) == 1)

# 加载
files = list(__import__('glob').glob(f"{tmp}/*.rec"))
entries = load_recording(files[0])
check("R1.3 load", len(entries) >= 7, f"loaded={len(entries)}")

# 提取互动
ixs = extract_interactions(entries)
check("R1.4 extract interactions", len(ixs) >= 2, f"ix={len(ixs)}")
check("R1.5 interaction has trigger", ixs[0].get("trigger") is not None)
check("R1.6 interaction has s2", ixs[0].get("s2_reply") is not None)

shutil.rmtree(tmp)

# ═══════════════════════════════════════════════════
# R2: 评分器 (mock)
# ═══════════════════════════════════════════════════
print("\n── R2: 评分器 ──")
from src.iteration.scorer import Phase2Scorer, ScoredInteraction
from src.models.s2_client import DeepSeekClient, S2Response

# Mock S2返回评分JSON
s2m = DeepSeekClient(mock_mode=True, api_key="test")
await_ = asyncio.get_event_loop().run_until_complete
s2m._mock_responses = [S2Response(
    content='[{"id":1,"persona_consistency":8,"fun_factor":7,"timing":9,"engagement":6,"s1_misjudge":false,"persona_drift":false,"reusable":true}]',
    total_ms=100,
)]
await_(s2m.start())

scorer = Phase2Scorer(s2m)
dummy_ix = [{
    "trigger": {"text": "主播好"},
    "s1_decision": {"token": "Start-Speaking"},
    "s2_reply": {"content": "来了来了"},
    "reactions": [{"text": "好好好"}, {"text": "主播回我了"}],
}]
scores = await_(scorer.score_batch(dummy_ix))
check("R2.1 scoring works", len(scores) == 1, f"got={len(scores)}")
if scores:
    check("R2.2 score fields", scores[0].persona_consistency > 0)
    check("R2.3 reusable flag", scores[0].reusable)

summary = scorer.summarize(scores)
check("R2.4 summary", summary.get("total") == 1, f"summary={summary}")

await_(s2m.stop())

# ═══════════════════════════════════════════════════
# R3: 提炼器
# ═══════════════════════════════════════════════════
print("\n── R3: 提炼器 ──")
from src.iteration.extractor import Phase3Extractor

ext = Phase3Extractor()
scored = [ScoredInteraction(
    trigger_text="t1", reply_text="r1",
    persona_consistency=8, fun_factor=8, timing=9, engagement=7,
    reusable=True,
), ScoredInteraction(
    trigger_text="t2", reply_text="bad reply",
    persona_consistency=3, fun_factor=2, timing=5, engagement=2,
    s1_misjudge=True,
)]
result = ext.extract(scored)
check("R3.1 extraction", len(result.summary) > 0, result.summary)
check("R3.2 has skill", len(result.skills) >= 1)
check("R3.3 has rule", len(result.rules) >= 1)

# ═══════════════════════════════════════════════════
# R4: 注入器
# ═══════════════════════════════════════════════════
print("\n── R4: 注入器 ──")
from src.iteration.injector import Phase4Injector

tmp2 = tempfile.mkdtemp()
inj = Phase4Injector(tmp2)

# 验证
report = inj.validate(result)
check("R4.1 validate", len(report.approved) + len(report.pending) + len(report.rejected) > 0,
      f"approved={len(report.approved)} pending={len(report.pending)} rejected={len(report.rejected)}")

# 应用
applied = inj.apply(report)
check("R4.2 apply", applied)
check("R4.3 history", len(inj.history) == 1)

shutil.rmtree(tmp2)

# ═══════════════════════════════════════════════════
# R5: 冷启动
# ═══════════════════════════════════════════════════
print("\n── R5: 冷启动 ──")
from src.content.coldstart import ColdStartManager
from src.prompts.assembler import PromptAssembler

tmp3 = tempfile.mkdtemp()
pa = PromptAssembler()
cs = ColdStartManager(pa, s2m, tmp3)
check("R5.1 learning mode", cs.is_learning_mode())
check("R5.2 skill count 0", cs.skill_count == 0)

shutil.rmtree(tmp3)

# ═══════════════════════════════════════════════════
# R6: 录制集成到主循环
# ═══════════════════════════════════════════════════
print("\n── R6: 录制集成 ──")
from src.main import AIStreamer

async def test_recorder_integration():
    s = AIStreamer()
    s._s1._client._mock_mode = True
    s._s2._mock_mode = True
    # 启用录制
    s._recorder = Recorder("data/recordings")
    s._recorder.start("integration_test")
    await s.start()

    from src.models.s2_client import S2Response
    s._s2.set_mock_responses([S2Response(content="mock", total_ms=100)])

    s._s1._client.set_mock_responses(["<|Quick-Reply|> hi"])
    await s.handle_message({"user":"test","user_id":"u1","text":"hello","mentioned_bot":True})

    s._s1.reset()
    s._s1._client.set_mock_responses(["<|Start-Speaking confidence=0.7|> dir"])
    await s.handle_message({"user":"test2","user_id":"u2","text":"question","is_question":True})

    count = s._recorder.stop()
    await s.stop()
    check("R6.1 recorded in streamer", count >= 6, f"entries={count}")
    check("R6.2 rec file exists", len(list(__import__('glob').glob("data/recordings/*.rec"))) >= 1)

asyncio.run(test_recorder_integration())

print(f"\nTOTAL: {p+f} | PASS={p} | FAIL={f}")
if f: sys.exit(1)
print("PHASE 4: ALL TESTS PASSED")
