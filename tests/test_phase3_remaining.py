"""Phase 3 测试: 视觉 + 情绪 + 多平台 (更新版)"""

import os, sys, time, asyncio, numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DEEPSEEK_API_KEY"] = "test-key"
p = f = 0


def check(name, condition, detail=""):
    global p, f
    if condition:
        p += 1
        print(f"  [OK] {name}{' — ' + str(detail) if detail else ''}")
    else:
        f += 1
        print(f"  [FAIL] {name}{' — ' + str(detail) if detail else ''}")


print("\n── V1: 视觉 ──")
from src.vision.pipeline import VisualPipeline
from src.vision.recognizer import VisionRecognizer

vp = VisualPipeline(fps=10, resolution=64, use_vit=False)
check("V1.1 init", vp.state is not None)
check("V1.2 state fields", vp.state.summary == "未知画面")

# 启发式
dark = np.zeros((64, 64, 3), dtype=np.uint8)
r = VisionRecognizer.basic_detect(dark)
check(
    "V1.3 dark screen",
    "黑屏" in r.description or "加载" in r.description,
    r.description,
)

bright = np.ones((64, 64, 3), dtype=np.uint8) * 200
r2 = VisionRecognizer.basic_detect(bright)
check("V1.4 bright screen", r2.description != "")

# ViT
check("V1.5 ViT build_context", len(VisionRecognizer().build_visual_context(r)) > 0)

# 变化检测
f1 = np.zeros((64, 64, 3), dtype=np.uint8)
f2 = np.ones((64, 64, 3), dtype=np.uint8) * 255
vp._prev_frame = f1
check("V1.6 changed", vp._detect_change(f2))
vp._prev_frame = f2
check("V1.7 not changed", not vp._detect_change(f2))

# 回调
called = []


async def cb(d, s, c):
    called.append(d)


vp.on_change(cb)
check("V1.8 change callback", len(vp._on_change_callbacks) >= 1)

print("\n── E1: 情绪 ──")
from src.emotion.model import EmotionalState

em = EmotionalState()
em.trigger("big_gift")
check("E1.1 gift trigger", em.valence > 0.3)
em.trigger("insult")
check("E1.2 insult trigger", em.valence < 0.4, f"v={em.valence:.2f}")
em.apply(2.0, 2.0, 2.0)
check("E1.3 clamp", em.valence == 1.0 and em.arousal == 1.0)
em.reset()
check("E1.4 reset", em.valence == 0.10)
check("E1.5 prompt string", len(em.to_prompt_str()) > 0)
check("E1.6 speak modifier", isinstance(em.speak_threshold_modifier, float))

print("\n── M1: 多平台 ──")
from src.platform.bilibili import BilibiliAdapter
from src.platform.base import UnifiedMessage

ba = BilibiliAdapter(room_id=4538234)
raw = {"user": "小明", "user_id": "u123", "text": "主播好", "event_type": "chat"}
norm = ba.normalize(raw)
check("M1.1 normalize", norm.user == "小明" and norm.platform == "bilibili")
raw2 = {
    "user": "老板",
    "user_id": "u456",
    "text": "",
    "event_type": "gift",
    "price": 100,
}
norm2 = ba.normalize(raw2)
check("M1.2 gift normalize", norm2.event_type == "gift" and norm2.monetary_value == 100)
check("M1.3 language zh", ba._detect_language("你好") == "zh")
check("M1.4 language en", ba._detect_language("hello world test") == "en")

print("\n── I1: 集成 ──")
from src.main import AIStreamer


async def t():
    s = AIStreamer()
    s._s1._client._mock_mode = True
    s._s2._mock_mode = True
    await s.start()
    s._s1._client.set_mock_responses(["<|Quick-Reply|> hi"])
    await s.handle_message({"user": "test", "user_id": "u1", "text": "hello"})
    check("I1.1 emotion", s._emotion is not None)
    await s.stop()


asyncio.run(t())

print(f"\nTOTAL: {p + f} | PASS={p} | FAIL={f}")
if f:
    sys.exit(1)
print("PHASE 3: ALL PASSED")
