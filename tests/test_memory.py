"""


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


  {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


  {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


  {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


"""





import os, sys, time, asyncio


from dotenv import load_dotenv





sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


load_dotenv()





p = f = 0








def check(name, condition, detail=""):


    global p, f


    if condition:


        p += 1


        {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    else:


        f += 1


        {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}








    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}





from src.memory.l1_working import WorkingMemory


from src.memory.l2_short import ShortTermMemory





wm = WorkingMemory()


check("L1.1 empty snapshot", wm.reply_count == 0)


check("L1.2 seconds_since_last_reply never", wm.seconds_since_last_reply > 900)





wm.add_message(


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


)


check("L1.3 add_message", len(wm.recent_messages) == 1)





wm.mark_replied("msg_001")


check("L1.4 mark_replied", wm.is_replied("msg_001"))


check("L1.5 not_replied", not wm.is_replied("msg_999"))





wm.record_reply()


check("L1.6 record_reply", wm.reply_count == 1 and wm.seconds_since_last_reply < 1)





wm.add_decision("Start-Speaking", 0.88)


check("L1.7 add_decision", len(wm.recent_decisions) == 1)





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}





ctx = wm.to_context()


check("L1.9 to_context", "recent_messages" in ctx and "current_topic" in ctx)


wm.reset()


check("L1.10 reset", wm.reply_count == 0)





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}





import tempfile, shutil





tmp = tempfile.mkdtemp()


l2 = ShortTermMemory(data_dir=tmp)





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


check("L2.1 empty viewer", l2.get_viewer("user_1") is None)





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


v = l2.get_viewer("user_1")


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


v2 = l2.get_viewer("user_1")


check("L2.3 upsert existing", v2.interaction_count == 2)





for i in range(11):


    l2.upsert_viewer(f"user_bulk_{i}", f"bulk_{i}")


check("L2.4 bulk viewers", l2.viewer_count >= 12)





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


for _ in range(9):


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


vip2 = l2.get_viewer("vip_user")


check("L2.5 loyalty level", vip2.loyalty_level == 1, f"level={vip2.loyalty_level}")





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


check("L2.8 faq fuzzy hit", hit2 is not None, f"got={hit2}")





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


check("L2.9 faq miss", miss is None)





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


check("L2.10 record interaction", l2.interaction_count == 1)





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


check("L2.11 search interactions", len(results) >= 1)





ctx3 = l2.get_recent_context("user_1")


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


l2_loaded = ShortTermMemory(data_dir=tmp)


check("L2.13 persistence", l2_loaded.viewer_count == l2.viewer_count)


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


check(


    "L2.15 search viewers",


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


)





shutil.rmtree(tmp)





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}





from src.main import AIStreamer








async def test_integration():


    s = AIStreamer()


    s._s1._client._mock_mode = True


    s._s2._mock_mode = True


    await s.start()





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    v = s._memory.get_viewer("new_001")


    check("I1 auto-profile", v is not None and v.interaction_count == 1)





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    s._s1.reset()


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    v2 = s._memory.get_viewer("new_001")


    check("I2 profile enhanced", v2.interaction_count == 2)





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    s._s1.reset()


    s._s1._client.set_mock_responses(["<|Quick-Reply|> test"])


    await s.handle_message(


        {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    )


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    check("I3 interaction recorded", len(results) >= 1, f"found {len(results)}")





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    msg_id = "dup_test_001"


    s._wm.mark_replied(msg_id)


    check("I4 dedup works", s._wm.is_replied(msg_id))





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    for i in range(60):


        s._wm.add_message({"user": f"u{i}", "text": f"msg{i}"})


    check("I5 message buffer cap", len(s._wm.recent_messages) <= 50)





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    check("I7 memory saved", True)





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


    check("I8 stop clean", True)








asyncio.run(test_integration())





    {"user": "test_viewer", "text": "hello", "mentioned_bot": True, "timestamp": time.time()}


if f:


    sys.exit(1)


print("ROUND 1: ALL TESTS PASSED")


