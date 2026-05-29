"""
OpenNeuro TTS 引擎 — Comni Bridge (llama.cpp-omni API)

S1/S2 回复统一通过 Comni 的 MiniCPM-o TTS 输出语音。
Usage:
    from src.tts import get_comni_bridge
    bridge = await get_comni_bridge()
    await bridge.speak("こんにちは")
"""

from src.tts.comni_bridge import ComniTTSBridge, ComniTTSConfig, get_comni_bridge

__all__ = ["ComniTTSBridge", "ComniTTSConfig", "get_comni_bridge"]
