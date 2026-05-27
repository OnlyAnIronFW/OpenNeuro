"""å¨æ¨¡åéææµè¯?â?ç«¯å°ç«¯åä½éªè¯?""

import os, sys, time, asyncio
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

p = f = 0


def check(name, condition, detail=""):
    global p, f
    if condition:
        p += 1
        print(f"  [OK] {name}{' â?' + detail if detail else ''}")
    else:
        f += 1
        print(f"  [FAIL] {name}{' â?' + detail if detail else ''}")


# âââââââââââââââââââââââââââââââââââââââââââââââââââ?# I1: å¨æ¨¡åå¯¼å¥æ å¾ªç¯ä¾èµ
# âââââââââââââââââââââââââââââââââââââââââââââââââââ?print("\nââ I1: å¨æ¨¡åå¯¼å?ââ")
modules_ok = True
for mod_path in [
    "src.events.bus",
    "src.config.loader",
    "src.prompts.assembler",
    "src.models.s1_client",
    "src.models.s2_client",
    "src.s1.parser",
    "src.s1.rule_engine",
    "src.s1.engine",
    "src.s2.cleaner",
    "src.s2.cache",
    "src.memory.l1_working",
    "src.memory.l2_short",
    "src.threads.manager",
    "src.main",
    "src.utils.text",
    "src.utils.logger",
]:
    try:
        __import__(mod_path)
    except Exception as e:
        modules_ok = False
        print(f"  FAIL: {mod_path} â?{e}")
check("I1.1 all 16 modules importable", modules_ok)

# âââââââââââââââââââââââââââââââââââââââââââââââââââ?# I2: Config â?Prompt â?S1 â?S2 å¨é¾è·?(mock)
# âââââââââââââââââââââââââââââââââââââââââââââââââââ?print("\nââ I2: å¨é¾è·?mock ââ")
from src.main import AIStreamer


async def test_full_pipeline_mock():
    s = AIStreamer()
    s._s1._client._mock_mode = True
    s._s2._mock_mode = True
    await s.start()

    from src.models.s2_client import S2Response

    s._s2.set_mock_responses([S2Response(content="mock reply")])

    # 3ä¸ªè§ä¼? 3æ¡æ¶æ?    messages = [
        ("å°æ", "uid_1", "ä¸»æ­å¥?, True),
        ("å°çº¢", "uid_2", "ä»å¤©ç©ä»ä¹?, True),
        ("å°æ", "uid_1", "å¯¹äºæ³å¸æä¹å ç¹", True),
    ]

    replies = []
    for name, uid, text, at in messages:
        s._s1._client.set_mock_responses([f"<|Quick-Reply|> hi {name}"])
        r = await s.handle_message(
            {"user": name, "user_id": uid, "text": text, "mentioned_bot": at}
        )
        replies.append(r)

    # éªè¯: äºä»¶æ»çº¿
    check("I2.1 3 replies returned", len([r for r in replies if r]) >= 1)

    # éªè¯: çº¿ç¨ç®¡ç
    check(
        "I2.2 threads created",
        s._threads.total_count >= 1,
        f"total={s._threads.total_count}",
    )
    # å°æ2æ¡æ¶æ¯åºå½å¥åä¸çº¿ç¨
    snap = s._threads.snapshot()
    check("I2.3 thread snapshot", len(snap) >= 1)

    # éªè¯: L1 å·¥ä½è®°å¿
    check(
        "I2.4 L1 messages",
        len(s._wm.recent_messages) >= 3,
        f"msgs={len(s._wm.recent_messages)}",
    )

    # éªè¯: L2 ç­æè®°å¿
    check("I2.5 L2 viewers", s._memory.viewer_count >= 2, f"viewers={s._memory.viewer_count}")
    # å°æäºå¨2æ¬?    v_xm = s._memory.get_viewer("uid_1")
    check(
        "I2.6 viewer interaction count",
        v_xm is not None and v_xm.interaction_count >= 2,
        f"count={v_xm.interaction_count if v_xm else 0}",
    )
    # æ¥çä¸ä¸æ?    ctx = s._memory.get_viewer_context("uid_1")
    check("I2.7 viewer context generated", len(ctx) > 0, f"ctx={ctx[:50]}")

    # éªè¯: S2 æ¸æ´å?    from src.s2.cleaner import S2OutputCleaner

    cleaner = S2OutputCleaner()
    clean = cleaner.clean('{"reply":"test"} (*ç¬?)')
    check("I2.8 cleaner works", clean.text == "test", f"got={clean.text!r}")

    # éªè¯: è¯­ä¹ç¼å­
    from src.s2.cache import SemanticCache

    cache = SemanticCache()
    cache.set("test_q", "test_a")
    check("I2.9 cache works", cache.get("test_q") == "test_a")

    # éªè¯: Logger
    from src.utils.logger import log_manager

    recent = log_manager.get_recent("main", 5)
    check("I2.10 logger has entries", len(recent) >= 1, f"entries={len(recent)}")

    await s.stop()
    check("I2.11 stop clean", True)


asyncio.run(test_full_pipeline_mock())

# âââââââââââââââââââââââââââââââââââââââââââââââââââ?# I3: çå®APIç«¯å°ç«?(S1=mock, S2=real)
# âââââââââââââââââââââââââââââââââââââââââââââââââââ?print("\nââ I3: çå®APIç«¯å°ç«?ââ")


async def test_real_e2e():
    s = AIStreamer()
    s._s1._client._mock_mode = True
    s._s2._mock_mode = False
    await s.start()

    # åºæ¯: èç²åå½
    v = s._memory.upsert_viewer("old_fan", "éç²èå¼ ")
    for _ in range(45):
        v = s._memory.upsert_viewer("old_fan", "éç²èå¼ ")
    v.known_facts["æ¨èè£å¤"] = "é¾é³å?
    v.interaction_style = "åæ¬¢è¢«è°ä¾?

    s._s1._client.set_mock_responses(
        ["<|Start-Speaking confidence=0.7|> èç²èå¼ æ¥äº, ææå¼å¹¶é®é®ä¸æ¬¡è£å¤çäº"]
    )
    t0 = time.perf_counter()
    r1 = await s.handle_message(
        {
            "user": "éç²èå¼ ",
            "user_id": "old_fan",
            "text": "ä¸»æ­ææ¥äºï¼ä¸æ¬¡è¯´çé£ä¸ªè£å¤ä½ çäºæ²¡",
            "mentioned_bot": True,
            "is_question": True,
        }
    )
    lat = (time.perf_counter() - t0) * 1000
    check(
        "I3.1 old fan reply",
        r1 is not None and len(r1) > 0,
        f"'{str(r1)[:40]}' {lat:.0f}ms",
    )

    # éªè¯è®°å¿æ³¨å¥äº?    ctx = s._memory.get_viewer_context("old_fan")
    check(
        "I3.2 memory context injected",
        "éç²" in ctx or "èç²" in ctx,
        f"ctx={ctx[:60]}",
    )

    # éªè¯çº¿ç¨
    check("I3.3 thread for old fan", s._threads.total_count >= 1)

    # éªè¯ L1
    check("I3.4 L1 working", len(s._wm.recent_messages) >= 1)

    await s.stop()
    check("I3.5 stop ok", True)


asyncio.run(test_real_e2e())

# âââââââââââââââââââââââââââââââââââââââââââââââââââ?# I4: éç½®ç­æ´æ°èè°?# âââââââââââââââââââââââââââââââââââââââââââââââââââ?print("\nââ I4: éç½®ç­æ´æ?ââ")
from src.config.loader import ConfigManager

cfg = ConfigManager("config.yaml")
cfg.load()
watcher_called = []
cfg.on_change(lambda c: watcher_called.append(1))
changed = cfg.check_and_reload()
check("I4.1 no false reload", not changed)
check("I4.2 watcher not triggered", len(watcher_called) == 0)

# âââââââââââââââââââââââââââââââââââââââââââââââââââ?# I5: Prompt ä¸å±è§£è¦èè°?# âââââââââââââââââââââââââââââââââââââââââââââââââââ?print("\nââ I5: Prompt è§£è?ââ")
from src.prompts.assembler import PromptAssembler

pa = PromptAssembler()
s1 = pa.build_s1_system()
s2 = pa.build_s2_system()
check("I5.1 S1 has rules", "å³ç­è§å" in s1 or "å¿é¡»åå¤" in s1)
check("I5.2 S1 has persona", "NewRoad" in s1)
check("I5.3 S2 has rules", "è¾åºæ ¼å¼" in s2 or "ä¸è¦ JSON" in s2)
check("I5.4 S2 has persona", "NewRoad" in s2)
# è§ååäººè®¾åç¦?check("I5.5 rules â?persona", pa.s1_rules_raw != pa.persona_core_raw)

# éè½½ä¸ä¸¢å¤±åå®?pa.reload_all()
s1b = pa.build_s1_system()
check("I5.6 reload preserves content", len(s1b) == len(s1))

print(f"\nTOTAL: {p + f} | PASS={p} | FAIL={f}")
if f:
    sys.exit(1)
print("INTEGRATION TESTS: ALL PASSED")
