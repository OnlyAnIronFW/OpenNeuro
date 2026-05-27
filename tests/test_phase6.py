"""Phase 6: S1微调管线测试"""
import os, sys, time, asyncio, json, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['DEEPSEEK_API_KEY'] = 'test-key'
p = f = 0
def check(name, condition, detail=""):
    global p, f
    if condition: p += 1; print(f"  [OK] {name}{' — '+str(detail) if detail else ''}")
    else: f += 1; print(f"  [FAIL] {name}{' — '+str(detail) if detail else ''}")

# ═══════════════════════════════════════════════════
# T1: 数据收集
# ═══════════════════════════════════════════════════
print("\n── T1: 数据收集 ──")
from src.iteration.s1_trainer import S1TrainingCollector

tmp = tempfile.mkdtemp()
tc = S1TrainingCollector(tmp)
tc.start_session("test_session")

# 模拟S1决策
s1 = tc.record(
    [{"user":"小明","text":"主播好"}],
    "<|Start-Speaking confidence=0.8|> 打招呼",
    "Start-Speaking", 0.8,
)
s2 = tc.record(
    [{"user":"路人","text":"666"}],
    "<|Continue-Listening|>",
    "Continue-Listening", 0.0,
)
check("T1.1 2 samples", tc.sample_count == 2)
check("T1.2 stats", tc.stats["total"] == 2)

# 标记修正
tc.mark_correction(1, "Quick-Reply", "666应该回个6")
check("T1.3 correction", tc.stats["corrected"] == 1)
check("T1.4 misjudged", tc.get_misjudged()[0].s1_token == "Continue-Listening")

# ═══════════════════════════════════════════════════
# T2: 导出
# ═══════════════════════════════════════════════════
print("\n── T2: 导出 ──")

# Alpaca
ap = tc.export_alpaca()
check("T2.1 alpaca file", os.path.exists(ap))
data = json.loads(open(ap, encoding='utf-8').read())
check("T2.2 alpaca entries", len(data) >= 1, f'{len(data)}')

# ShareGPT
sg = tc.export_sharegpt()
check("T2.3 sharegpt file", os.path.exists(sg))
data2 = json.loads(open(sg, encoding='utf-8').read())
check("T2.4 sharegpt entries", len(data2) >= 1, f'{len(data2)}')
if data2:
    check("T2.5 sharegpt format", "conversations" in data2[0])

# JSONL
jl = tc.export_jsonl()
check("T2.6 jsonl file", os.path.exists(jl))
lines = open(jl, encoding='utf-8').readlines()
check("T2.7 jsonl lines", len(lines) == 2, f'{len(lines)}')

# ═══════════════════════════════════════════════════
# T3: 集成到主循环
# ═══════════════════════════════════════════════════
print("\n── T3: 集成 ──")
from src.main import AIStreamer

async def test_integration():
    s = AIStreamer()
    s._s1._client._mock_mode = True
    s._s2._mock_mode = True
    await s.start()

    s._s1._client.set_mock_responses(["<|Quick-Reply|> hi"])
    await s.handle_message({"user":"test","user_id":"u1","text":"hello"})
    
    check("T3.1 trainer has samples", s._trainer.sample_count >= 1,
          f'count={s._trainer.sample_count}')
    
    # 导出
    path = s._trainer.export_jsonl(str(tmp / "integration.jsonl"))
    check("T3.2 exported", os.path.exists(path))
    
    await s.stop()

asyncio.run(test_integration())

shutil.rmtree(tmp)

print(f"\nTOTAL: {p+f} | PASS={p} | FAIL={f}")
if f: sys.exit(1)
print("PHASE 6: ALL PASSED")
