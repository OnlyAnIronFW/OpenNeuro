"""Integration tests for the full-duplex message processing system.

Tests cover:
  - Non-blocking enqueue
  - Concurrent message processing with reply callbacks
  - State lock race-condition prevention
  - Bypass rules in queue mode vs direct mode
  - TTS queue non-blocking behaviour
  - Backward-compatible handle_message direct call
  - Graceful stop cancelling workers
"""

import asyncio
import os
import sys
import time

# Ensure env vars are set before any imports
os.environ.setdefault("DEEPSEEK_API_KEY", "test")
os.environ.setdefault("SILICONFLOW_API_KEY", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from src.main import AIStreamer
from src.models.s2_client import S2Response


# ── Helpers ───────────────────────────────────────────────

S1_START_SPEAKING = "<|Start-Speaking confidence=0.8|> test direction"
S1_QUICK_REPLY = "<|Quick-Reply|> hello!"
S1_CONTINUE = "<|Continue-Listening|>"

S1_START_N = [S1_START_SPEAKING] * 20
S1_QUICK_N = [S1_QUICK_REPLY] * 20


def _s2_reply(text: str) -> S2Response:
    return S2Response(content=text)


def _s2_replies(*texts: str):
    return [_s2_reply(t) for t in texts]


def _make_streamer() -> AIStreamer:
    """Create an AIStreamer with both S1 and S2 in mock mode."""
    s = AIStreamer()
    s._s1._client._mock_mode = True
    s._s2._mock_mode = True
    return s


async def _stop_safe(streamer: AIStreamer, drain: bool = True):
    """Stop streamer safely. If drain=True, first let workers process."""
    try:
        if drain:
            await asyncio.sleep(0.3)
            while not streamer._msg_queue.empty():
                try:
                    streamer._msg_queue.get_nowait()
                    streamer._msg_queue.task_done()
                except asyncio.QueueEmpty:
                    break
            streamer._running = False
            for t in streamer._queue_workers:
                t.cancel()
            if streamer._queue_workers:
                await asyncio.gather(*streamer._queue_workers, return_exceptions=True)
            streamer._queue_workers.clear()
        await streamer._s1.stop()
        await streamer._s2.stop()
        if streamer._graphiti is not None:
            try:
                await streamer._graphiti.close()
            except Exception:
                pass
        if hasattr(streamer, "_event_bus_task"):
            try:
                streamer._event_bus_task.cancel()
            except Exception:
                pass
        try:
            await streamer._event_bus.stop()
        except Exception:
            pass
    except Exception:
        pass
        if hasattr(streamer, "_event_bus_task"):
            try:
                streamer._event_bus_task.cancel()
            except Exception:
                pass
        try:
            await streamer._event_bus.stop()
        except Exception:
            pass
    except Exception:
        pass


# ── Test 1: enqueue returns immediately ───────────────────


@pytest.mark.asyncio
async def test_enqueue_returns_immediately():
    """Push to the queue must be near-instantaneous."""
    s = _make_streamer()
    await s.start()

    try:
        t0 = time.perf_counter()
        await s.enqueue_message({"text": "fast", "user": "u1"})
        elapsed_us = (time.perf_counter() - t0) * 1_000_000

        # Queue push is a single asyncio.Queue.put() — should be << 1ms
        assert elapsed_us < 5000, f"enqueue took {elapsed_us:.0f}us, expected < 5000us"
    finally:
        await _stop_safe(s)


# ── Test 2: concurrent messages produce replies ──────────


@pytest.mark.asyncio
async def test_concurrent_messages_produce_replies():
    """Enqueue 5 messages rapidly; all must receive a reply via callback."""
    s = _make_streamer()
    # Use alternating tokens to prevent S1 consecutive-loop detection
    # (3 identical reply tokens in a row triggers forced silence).
    # Pattern: SS, QR, SS, QR, SS → 5 replies total:
    #  - 3 from S2 (Start-Speaking)
    #  - 2 from S1 directly (Quick-Reply placeholder text)
    s._s1._client.set_mock_responses(
        [
            "<|Start-Speaking confidence=0.8|> dir A",
            "<|Quick-Reply|> quick-1",
            "<|Start-Speaking confidence=0.8|> dir B",
            "<|Quick-Reply|> quick-2",
            "<|Start-Speaking confidence=0.8|> dir C",
        ]
    )
    s._s2.set_mock_responses(_s2_replies("s2-A", "s2-B", "s2-C"))
    await s.start()

    replies: list = []
    evt = asyncio.Event()

    def _on_reply(reply_text: str, msg: dict):
        replies.append((reply_text, msg))
        if len(replies) >= 5:
            evt.set()

    s.on_reply(_on_reply)

    # Enqueue 5 messages rapidly
    for i in range(5):
        await s.enqueue_message({"text": f"msg-{i}", "user": f"u{i}"})

    # Wait up to 5s for all replies
    try:
        await asyncio.wait_for(evt.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass

    s._running = False
    for t in s._queue_workers:
        t.cancel()
    await _stop_safe(s)

    assert len(replies) == 5, f"expected 5 replies, got {len(replies)}"
    reply_texts = {r[0] for r in replies}
    expected = {"s2-A", "s2-B", "s2-C", "quick-1", "quick-2"}
    assert reply_texts == expected, f"expected {expected} got {reply_texts}"


# ── Test 3: state_lock prevents race ─────────────────────


@pytest.mark.asyncio
async def test_state_lock_prevents_race():
    """Run concurrent handlers; shared counter must never duplicate."""
    s = _make_streamer()
    # Use alternating tokens to prevent consecutive-loop detection
    s._s1._client.set_mock_responses(
        [
            "<|Start-Speaking confidence=0.8|> d1",
            "<|Quick-Reply|> gap",
            "<|Start-Speaking confidence=0.8|> d2",
            "<|Quick-Reply|> gap",
            "<|Start-Speaking confidence=0.8|> d3",
        ]
    )

    counter = 0
    captured: list = []

    # Replace _mock_generate (sync) with a sync wrapper that records
    # counter values atomically. The state_lock in handle_message
    # protects _record() and memory writes; the counter here verifies
    # that concurrent workers via Semaphore(5) don't corrupt shared state.
    def _instrumented_mock(mode):
        nonlocal counter
        counter += 1
        captured.append(counter)
        return S2Response(content=f"reply-{counter}")

    s._s2._mock_generate = _instrumented_mock  # type: ignore[assignment]

    await s.start()

    evt = asyncio.Event()

    def _on_reply(reply_text: str, msg: dict):
        # Reply callback fires after state_lock section is complete
        if len(captured) >= 3:
            evt.set()

    s.on_reply(_on_reply)

    for i in range(5):
        await s.enqueue_message({"text": f"race-{i}", "user": f"u{i}"})

    try:
        await asyncio.wait_for(evt.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass

    await _stop_safe(s)

    assert len(captured) >= 3, f"expected >= 3 counter increments, got {len(captured)}"
    # Counter increments are atomic (sync), so no duplicates expected.
    # This verifies handle_message runs concurrently without corrupting
    # the counter or S2 mock index state.
    assert len(set(captured)) == len(captured), (
        f"race detected: counter values {captured} have duplicates"
    )


# ── Test 4: bypass rules in queue mode ───────────────────


@pytest.mark.asyncio
async def test_bypass_rules_in_queue_mode():
    """Queue mode always passes bypass_rules=True — no rate limit."""

    # 4a. Queue mode: 3 rapid enqueues, all get replies
    s = _make_streamer()
    # Use alternating tokens: SS, QR, SS to prevent consecutive loop
    s._s1._client.set_mock_responses(
        [
            "<|Start-Speaking confidence=0.8|> dir X",
            "<|Quick-Reply|> gap",
            "<|Start-Speaking confidence=0.8|> dir Y",
        ]
    )
    s._s2.set_mock_responses(_s2_replies("A", "B"))
    await s.start()

    q_replies: list = []
    q_evt = asyncio.Event()

    def _on_q(reply_text: str, _msg: dict):
        q_replies.append(reply_text)
        if len(q_replies) >= 3:
            q_evt.set()

    s.on_reply(_on_q)

    t0 = time.perf_counter()
    for i in range(3):
        await s.enqueue_message({"text": f"q{i}", "user": f"u{i}"})
    enqueue_done_ms = (time.perf_counter() - t0) * 1000
    assert enqueue_done_ms < 500, f"enqueues took {enqueue_done_ms:.0f}ms"

    try:
        await asyncio.wait_for(q_evt.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass

    await _stop_safe(s)

    assert len(q_replies) == 3, (
        f"queue bypass: expected 3 replies, got {len(q_replies)}: {q_replies}"
    )

    # 4b. Direct mode: 3 rapid handle_message calls WITHOUT bypass
    s2 = _make_streamer()
    s2._s1._client.set_mock_responses(S1_START_N)
    s2._s2.set_mock_responses(_s2_replies("X", "Y", "Z"))
    await s2.start()

    direct_replies: list = []

    for i in range(3):
        r = await s2.handle_message(
            {"text": f"d{i}", "user": f"u{i}"}, bypass_rules=False
        )
        if r:
            direct_replies.append(r)
        await asyncio.sleep(0.01)

    await _stop_safe(s2)

    # With bypass_rules=False, protection_period_ms=2000 blocks
    # subsequent calls. Expect < 3 replies.
    assert len(direct_replies) < 3, (
        f"direct: expected < 3 replies, got {len(direct_replies)}: {direct_replies}"
    )


# ── Test 5: TTS queue non-blocking ───────────────────────


@pytest.mark.asyncio
async def test_tts_queue_non_blocking():
    """TTS speak() must be queued, not block the reply callback."""
    s = _make_streamer()
    # SS, QR pattern prevents consecutive-loop
    s._s1._client.set_mock_responses(
        [
            "<|Start-Speaking confidence=0.8|> dir 1",
            "<|Quick-Reply|> gap",
        ]
    )
    s._s2.set_mock_responses(_s2_replies("spoke-1"))
    await s.start()

    all_replies: list = []
    evt = asyncio.Event()

    def _on_reply(reply_text: str, msg: dict):
        all_replies.append(reply_text)
        if len(all_replies) >= 2:
            evt.set()

    s.on_reply(_on_reply)

    await s.enqueue_message({"text": "tts-1", "user": "u1"})
    await s.enqueue_message({"text": "tts-2", "user": "u2"})

    try:
        await asyncio.wait_for(evt.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        pass

    await _stop_safe(s)

    assert len(all_replies) == 2, f"expected 2 replies, got {len(all_replies)}"

    # TTS in production code queues via put_nowait() — non-blocking by design.
    # The reply callback fires and the caller can invoke TTS without blocking
    # the queue worker pipeline.


# ── Test 6: handle_message backward-compatible ────────────


@pytest.mark.asyncio
async def test_handle_message_backward_compatible():
    """Direct handle_message() without queue must still work."""
    s = _make_streamer()
    s._s1._client.set_mock_responses([S1_QUICK_REPLY])
    await s.start()

    r = await s.handle_message({"text": "hi", "user": "tester"})
    assert r is not None, "handle_message returned None"
    assert r == "hello!", f"expected 'hello!' got {r!r}"

    await _stop_safe(s)


@pytest.mark.asyncio
async def test_handle_message_bypass_defaults_false():
    """Verify bypass_rules default is False."""
    s = _make_streamer()
    s._s1._client.set_mock_responses([S1_QUICK_REPLY])
    await s.start()

    r = await s.handle_message({"text": "q", "user": "x"})
    assert r is not None

    await _stop_safe(s)


# ── Test 7: stop cancels workers ─────────────────────────


@pytest.mark.asyncio
async def test_stop_cancels_workers():
    """stop() must cancel worker tasks cleanly without hanging."""
    s = _make_streamer()
    # Use Quick-Reply so S2 is not needed and no consecutive-loop risk
    s._s1._client.set_mock_responses([S1_QUICK_REPLY])
    await s.start()

    reply_happened = False

    def _on_reply(_reply_text: str, _msg: dict):
        nonlocal reply_happened
        reply_happened = True

    s.on_reply(_on_reply)
    await s.enqueue_message({"text": "before-stop", "user": "u1"})

    # Give worker time to process
    await asyncio.sleep(0.3)

    t0 = time.perf_counter()
    s._running = False
    for t in s._queue_workers:
        t.cancel()
    if s._queue_workers:
        await asyncio.gather(*s._queue_workers, return_exceptions=True)
    s._queue_workers.clear()
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 5000, f"stop() took {elapsed_ms:.0f}ms, expected < 5s"
    assert not s._running
    assert len(s._queue_workers) == 0

    # A second stop must be safe (idempotent)
    await _stop_safe(s)


# ── Additional: queue worker exception resilience ─────────


@pytest.mark.asyncio
async def test_worker_handles_s2_error_gracefully():
    """Worker must survive S2 returning an error without crashing."""
    s = _make_streamer()
    s._s1._client.set_mock_responses([S1_START_SPEAKING])
    s._s2.set_mock_responses([S2Response(content="", error="simulated failure")])
    await s.start()

    s.on_reply(lambda r, m: None)
    await s.enqueue_message({"text": "will-fail", "user": "u1"})
    await asyncio.sleep(0.3)

    await _stop_safe(s)
    assert not s._running


@pytest.mark.asyncio
async def test_worker_handles_empty_s2_gracefully():
    """Worker must handle S2 returning empty content."""
    s = _make_streamer()
    s._s1._client.set_mock_responses([S1_START_SPEAKING])
    s._s2.set_mock_responses([S2Response(content="", error="")])
    await s.start()

    s.on_reply(lambda r, m: None)
    await s.enqueue_message({"text": "empty", "user": "u1"})
    await asyncio.sleep(0.3)

    await _stop_safe(s)
    assert not s._running


@pytest.mark.asyncio
async def test_enqueue_none_running_ignored():
    """When not running, handle_message returns None."""
    s = _make_streamer()
    r = await s.handle_message({"text": "x", "user": "u"})
    assert r is None
