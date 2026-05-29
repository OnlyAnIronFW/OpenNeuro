"""
Comni TTS Bridge — 通过 llama.cpp-omni API 实现文本→语音

管道:
  omni_init (一次) → prefill(text) → decode (SSE) → WAV → 播放

Usage:
  bridge = ComniTTSBridge()
  await bridge.start()
  await bridge.speak("初めまして")
"""

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import urllib.request
import urllib.error

logger = logging.getLogger("comni_tts")


@dataclass
class ComniTTSConfig:
    enabled: bool = True
    llama_host: str = "http://localhost:19060"
    model_dir: str = r"F:\llm\models"
    tts_dir: str = r"F:\llm\models\tts"
    voice_audio: str = (
        r"F:\Comni\_internal\resources\apps\assets\ref_audio\ref_custom.wav"
    )
    output_dir: str = r"F:\OpenNeuro\data\tts_output"
    sample_rate: int = 24000


class ComniTTSBridge:
    """llama.cpp-omni TTS 桥接 — 一次 init, 多次 speak."""

    def __init__(self, config: Optional[ComniTTSConfig] = None):
        self._cfg = config or ComniTTSConfig()
        self._ready = False
        self._cnt = 0  # prefill counter
        self._play_lock = threading.Lock()

    # ──────────── 初始化 ────────────

    async def start(self) -> bool:
        """初始化 omni session, 加载 TTS 模块."""
        if not self._cfg.enabled:
            return False
        try:
            ok = await asyncio.to_thread(self._omni_init)
            if ok:
                self._ready = True
                logger.info("Comni TTS bridge ready")
            return ok
        except Exception as e:
            logger.error(f"Comni TTS init failed: {e}")
            return False

    def _omni_init(self) -> bool:
        url = f"{self._cfg.llama_host}/v1/stream/omni_init"
        data = json.dumps(
            {
                "media_type": 2,
                "use_tts": True,
                "duplex_mode": False,
                "model_dir": self._cfg.model_dir,
                "tts_bin_dir": self._cfg.tts_dir,
                "tts_gpu_layers": 100,
                "token2wav_device": "gpu:0",
                "output_dir": self._cfg.output_dir,
                "voice_audio": self._cfg.voice_audio,
            }
        ).encode()
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(
                    url, data=data, headers={"Content-Type": "application/json"}
                ),
                timeout=120,
            )
            resp = json.loads(r.read())
            ok = resp.get("success", False)
            self._cnt = 0
            logger.info(
                f"omni_init: {'OK' if ok else 'FAIL'}, voice={resp.get('voice_audio_used')}"
            )
            return ok
        except Exception as e:
            logger.error(f"omni_init error: {e}")
            return False

    def is_ready(self) -> bool:
        return self._ready

    # ──────────── 合成 ────────────

    async def speak(self, text: str) -> bool:
        """文本 → TTS → 播放.

        Returns:
            True if audio was generated and queued for playback.
        """
        if not self._ready or not text or len(text.strip()) < 2:
            return False

        try:
            ok = await asyncio.to_thread(self._speak_blocking, text)
            return ok
        except Exception as e:
            logger.error(f"speak error: {e}")
            return False

    def _speak_blocking(self, text: str) -> bool:
        """阻塞执行: prefill → decode → 收集 WAV → 播放."""
        # 1. Prefill
        self._cnt += 1
        if not self._prefill(text):
            return False

        # 2. Decode (SSE)
        wav_files = self._decode_and_collect()
        if not wav_files:
            return False

        # 3. Play
        self._play_wavs(wav_files)
        return True

    def _prefill(self, text: str) -> bool:
        url = f"{self._cfg.llama_host}/v1/stream/prefill"
        data = json.dumps(
            {
                "text": text,
                "cnt": self._cnt,
            }
        ).encode()
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(
                    url, data=data, headers={"Content-Type": "application/json"}
                ),
                timeout=10,
            )
            resp = json.loads(r.read())
            return resp.get("success", False)
        except Exception as e:
            logger.error(f"prefill error: {e}")
            return False

    def _decode_and_collect(self) -> list[Path]:
        """调用 decode SSE, 收集生成的 WAV 文件."""
        url = f"{self._cfg.llama_host}/v1/stream/decode"
        data = json.dumps({"stream": True}).encode()
        output_dir = Path(self._cfg.output_dir)

        # 记录 pre-decode 状态以便识别新文件
        existing = set()
        for rd in sorted(output_dir.glob("round_*")):
            existing.add(rd.name)

        try:
            r = urllib.request.urlopen(
                urllib.request.Request(
                    url, data=data, headers={"Content-Type": "application/json"}
                ),
                timeout=60,
            )
            # 等待 SSE 流完成
            buf = b""
            while True:
                chunk = r.read(4096)
                if not chunk:
                    break
                buf += chunk
            # 检查是否完成
            decoded = buf.decode(errors="replace")
            if "[DONE]" not in decoded:
                logger.warning("decode stream may be incomplete")
        except Exception as e:
            logger.error(f"decode error: {e}")
            return []

        # 找新生成的 round 目录
        new_rounds = []
        for rd in sorted(output_dir.glob("round_*")):
            if rd.name not in existing:
                new_rounds.append(rd)

        if not new_rounds:
            return []

        # 收集 WAV 文件
        wav_files = []
        for rd in new_rounds:
            tts_wav = rd / "tts_wav"
            if tts_wav.exists():
                wavs = sorted(tts_wav.glob("wav_*.wav"))
                wav_files.extend(wavs)

        return wav_files

    def _play_wavs(self, wav_files: list[Path]):
        """播放 WAV 文件序列."""
        if not wav_files:
            return

        with self._play_lock:
            import soundfile as sf
            import sounddevice as sd

            for wf in wav_files:
                try:
                    audio, sr = sf.read(str(wf))
                    sd.play(audio, sr)
                    sd.wait()
                except Exception as e:
                    logger.warning(f"play {wf.name}: {e}")
                    break

    # ──────────── 清理 ────────────

    async def stop(self):
        self._ready = False
        logger.info("Comni TTS bridge stopped")


# ── 全局单例 ─────────────────────────────────────────

_bridge: Optional[ComniTTSBridge] = None


async def get_comni_bridge() -> ComniTTSBridge:
    global _bridge
    if _bridge is None:
        _bridge = ComniTTSBridge()
        await _bridge.start()
    return _bridge
