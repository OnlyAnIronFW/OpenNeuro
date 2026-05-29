"""
Comni TTS Bridge — 监控 Comni TTS 输出, 播放新生成音频。

Comni 管理 llama-server 全生命周期。本 Bridge 通过:
  1. 对接 llama.cpp-omni API (omni_init → prefill → decode)
  2. 收集输出目录的 WAV 文件
  3. 播放

Usage:
  bridge = ComniTTSBridge()
  await bridge.start()
  await bridge.speak("text")
"""

import asyncio
import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
    def __init__(self, config: Optional[ComniTTSConfig] = None):
        self._cfg = config or ComniTTSConfig()
        self._ready = False
        self._cnt = 0
        self._play_lock = threading.Lock()

    async def start(self) -> bool:
        if not self._cfg.enabled:
            return False
        try:
            ok = await asyncio.to_thread(self._omni_init)
            self._ready = ok
            if ok:
                logger.info("Comni TTS bridge ready")
            return ok
        except Exception as e:
            logger.error(f"start failed: {e}")
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
            self._cnt = 0
            logger.info(f"omni_init OK, voice={resp.get('voice_audio_used')}")
            return resp.get("success", False)
        except urllib.error.HTTPError as e:
            if e.code == 500:
                logger.info("omni_init 500 — reusing existing session")
                self._cnt = len(list(Path(self._cfg.output_dir).glob("round_*"))) or 1
                return True
            logger.error(f"omni_init HTTP {e.code}")
            return False
        except Exception as e:
            logger.error(f"omni_init: {e}")
            return False

    def is_ready(self) -> bool:
        return self._ready

    async def speak(self, text: str) -> bool:
        if not self._ready or not text or len(text.strip()) < 2:
            return False
        try:
            return await asyncio.to_thread(self._speak_blocking, text)
        except Exception as e:
            logger.error(f"speak: {e}")
            return False

    def _speak_blocking(self, text: str) -> bool:
        self._cnt += 1
        if not self._prefill(text):
            return False
        wavs = self._decode_and_collect()
        if wavs:
            self._play_wavs(wavs)
            return True
        return False

    def _prefill(self, text: str) -> bool:
        url = f"{self._cfg.llama_host}/v1/stream/prefill"
        data = json.dumps({"text": text, "cnt": self._cnt}).encode()
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(
                    url, data=data, headers={"Content-Type": "application/json"}
                ),
                timeout=10,
            )
            return json.loads(r.read()).get("success", False)
        except Exception as e:
            logger.error(f"prefill: {e}")
            return False

    def _decode_and_collect(self) -> list[Path]:
        url = f"{self._cfg.llama_host}/v1/stream/decode"
        data = json.dumps({"stream": True}).encode()
        out = Path(self._cfg.output_dir)
        existing = {d.name for d in out.glob("round_*")}
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(
                    url, data=data, headers={"Content-Type": "application/json"}
                ),
                timeout=60,
            )
            while r.read(4096):
                pass
        except Exception as e:
            logger.error(f"decode: {e}")
            return []
        wavs = []
        for d in sorted(out.glob("round_*")):
            if d.name not in existing:
                for w in sorted((d / "tts_wav").glob("wav_*.wav")):
                    wavs.append(w)
        return wavs

    def _play_wavs(self, wavs: list[Path]):
        with self._play_lock:
            import soundfile as sf
            import sounddevice as sd

            for w in wavs:
                try:
                    a, s = sf.read(str(w))
                    sd.play(a, s)
                    sd.wait()
                    logger.info(f"play {w.name} ({len(a) / s:.1f}s)")
                except Exception as e:
                    logger.warning(f"play: {e}")

    async def stop(self):
        self._ready = False
        logger.info("Comni TTS bridge stopped")


_bridge: Optional[ComniTTSBridge] = None


async def get_comni_bridge() -> ComniTTSBridge:
    global _bridge
    if _bridge is None:
        _bridge = ComniTTSBridge()
        await _bridge.start()
    return _bridge
