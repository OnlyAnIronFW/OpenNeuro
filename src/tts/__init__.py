"""
MiniCPM-o 4.5 内置 TTS 引擎 — OpenNeuro 语音输出

持久化引擎, 一次加载, 多次合成。S1 和 S2 的输出统一走 MiniCPM TTS 生成语音。

Usage:
    from src.tts import MiniCPMTTSEngine, get_tts_engine, TTSConfig

    # 引擎单例 (推荐)
    engine = await get_tts_engine()
    audio = await engine.synthesize("こんにちは")
    engine.play(audio)

    # 或手动创建
    engine = MiniCPMTTSEngine(TTSConfig(enabled=True))
    await engine.start()
"""

from src.tts.engine import MiniCPMTTSEngine, get_tts_engine, TTSConfig

__all__ = ["MiniCPMTTSEngine", "get_tts_engine", "TTSConfig"]
