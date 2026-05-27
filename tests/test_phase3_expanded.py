"""Phase 3 扩测: 视觉边界 + 情绪压力 + 真实API情绪注入"""

import os, sys, time, asyncio
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

p = f = 0


def check(name, condition, detail=""):
    global p, f
    if condition:
        p += 1
        print(f"  [OK] {name}{' — ' + str(detail) if detail else ''}")
    else:
        f += 1
        print(f"  [FAIL] {name}{' — ' + str(detail) if detail else ''}")


# ═══════════════════════════════════════════════════
# V: 视觉扩测
# ═══════════════════════════════════════════════════
print("\n── V: 视觉扩测 ──")
from src.vision.pipeline import VisualPipeline, VisualEvent, EVENT_ACTIONS
import numpy as np

# V1: 全部10种事件映射
for ev in VisualEvent:
    act = EVENT_ACTIONS.get(ev, {})
    check(f"V.{ev.value}", "action" in act, act.get("action", "?"))

# V2: 快速帧率
vp = VisualPipeline(fps=30, resolution=32)
check("V2 fast fps", vp._fps == 30)

# V3: 变化检测边界
vp._prev_frame = None
check("V3 no prev frame", vp._detect_change(np.zeros((32, 32, 3), dtype=np.uint8)))

# V4: 画面无变化
f1 = np.ones((32, 32, 3), dtype=np.uint8) * 100
f2 = np.ones((32, 32, 3), dtype=np.uint8) * 100
vp._prev_frame = f1
check("V4 identical frames", not vp._detect_change(f2))

# V5: 小幅变化(<5%)
f3 = f1.copy()
f3[0:2, 0:2] = 200
vp._prev_frame = f1
check("V5 small change ignored", not vp._detect_change(f3))

# V6: 大幅变化(>5%)
f4 = np.ones((32, 32, 3), dtype=np.uint8) * 200
vp._prev_frame = f1
check("V6 large change detected", vp._detect_change(f4))

# V7: 手动事件不崩溃
for ev in VisualEvent:
    vp.simulate_event(ev)
check("V7 all events simulatable", True)

# ═══════════════════════════════════════════════════
# E: 情绪扩测
# ═══════════════════════════════════════════════════
print("\n── E: 情绪扩测 ──")
from src.emotion.model import EmotionalState, EMOTION_TRIGGERS

# E1: 所有触发器可用
check("E1 all triggers", len(EMOTION_TRIGGERS) >= 15)

# E2: 边界值钳制
em = EmotionalState()
em.apply(2.0, 2.0, 2.0)
check("E2 clamp high", em.valence == 1.0 and em.arousal == 1.0)
em.apply(-2.0, -2.0, -2.0)
check("E2 clamp low", em.valence == -1.0 and em.arousal == 0.0)

# E3: reset
em.valence = 0.9
em.arousal = 0.9
em.dominance = 0.9
em.reset()
check("E3 reset", em.valence == 0.10 and em.arousal == 0.25)

# E4: 连续触发不溢出
for _ in range(20):
    em.trigger("big_gift")
check("E4 consecutive triggers", em.valence >= 0.5)

# E5: 长期衰减
em5 = EmotionalState(valence=0.9, arousal=0.9)
em5.last_update = time.time() - 3600  # 1小时前
em5.decay()
check("E5 long decay", em5.valence < 0.5, f"v={em5.valence:.2f}")

# E6: 所有情绪状态查询
states = [em5.is_excited, em5.is_upset, em5.is_happy, em5.is_bored, em5.is_confident]
check("E6 all state queries", all(isinstance(s, bool) for s in states))

# ═══════════════════════════════════════════════════
# I: 真实API情绪注入
# ═══════════════════════════════════════════════════
print("\n── I: 真实API情绪注入 ──")
from src.main import AIStreamer


async def test_emotion_real():
    s = AIStreamer()
    s._s1._client._mock_mode = True
    s._s2._mock_mode = False
    await s.start()

    # 场景1: 开心时回复
    s._emotion.valence = 0.7
    s._emotion.arousal = 0.8
    s._s1._client.set_mock_responses(
        ["<|Start-Speaking confidence=0.6|> 心情好时的回复"]
    )
    r1 = await s.handle_message(
        {"user": "a", "user_id": "u1", "text": "主播今天心情不错？"}
    )
    check("I1 happy reply", r1 is not None and len(r1) > 3, str(r1)[:50])

    # 场景2: 沮丧时回复
    s._s1.reset()
    s._emotion.valence = -0.5
    s._emotion.arousal = 0.3
    s._s1._client.set_mock_responses(
        ["<|Start-Speaking confidence=0.7|> 心情差时的回复"]
    )
    r2 = await s.handle_message({"user": "b", "user_id": "u2", "text": "怎么不说话"})
    check("I2 upset reply", r2 is not None and len(r2) > 3, str(r2)[:50])

    # 场景3: 情绪从礼物触发后恢复
    s._s1.reset()
    # 送礼触发
    s._s1._client.set_mock_responses(["<|Quick-Reply|> 谢了"])
    await s.handle_message(
        {"user": "c", "user_id": "u3", "text": "", "event_type": "gift", "price": 50}
    )
    v_after_gift = s._emotion.valence
    check("I3 emotion after gift", v_after_gift > 0.10, f"v={v_after_gift:.2f}")

    await s.stop()


asyncio.run(test_emotion_real())

print(f"\nTOTAL: {p + f} | PASS={p} | FAIL={f}")
if f:
    sys.exit(1)
print("PHASE 3 EXPANDED: ALL PASSED")
