"""
Comni-omni TTS Bridge — 自管理实例, 单模型双功能.

架构:
  独立 llama.cpp-omni server (:19061)
  ├── /v1/chat/completions → S1 决策 (OpenNeuro 已指向此端口)
  └── /v1/stream/{omni_init|prefill|decode} → S1+S2 TTS

启动时检测 Comni :19060 → 若已运行则复用 (不占双份 VRAM).
否则启动自带 llama-server.

Usage:
  bridge = ComniTTSBridge()
  await bridge.start()
  await bridge.speak(text)   # S2 DeepSeek text → Roxy voice
  # S1: chat completions → server auto-generates speech tokens inline
"""

import asyncio, json, logging, os, subprocess, threading, time
import urllib.error, urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("comni_tts")


@dataclass
class ComniTTSConfig:
    enabled: bool = True
    server_bin: str = r"F:\Comni\_internal\resources\build\bin\Release\llama-server.exe"
    port: int = 19061
    host: str = "127.0.0.1"
    model_dir: str = r"F:\llm\models"
    llm_model: str = "MiniCPM-o-4_5-Q4_K_M.gguf"
    ctx_size: int = 4096
    n_gpu_layers: int = 99
    voice_audio: str = (
        r"F:\Comni\_internal\resources\apps\assets\ref_audio\ref_custom.wav"
    )
    output_dir: str = r"F:\OpenNeuro\data\tts_output"
    sample_rate: int = 24000

    @property
    def llama_host(self) -> str:
        return f"http://{self.host}:{self.port}"


class ComniTTSBridge:
    def __init__(self, config: Optional[ComniTTSConfig] = None):
        self._cfg = config or ComniTTSConfig()
        self._proc: Optional[subprocess.Popen] = None
        self._ready = False
        self._cnt = 0
        self._play_lock = threading.Lock()
        self._tts_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=10)
        self._playback_task: Optional[asyncio.Task] = None

    async def start(self) -> bool:
        if not self._cfg.enabled:
            return False
        try:
            if not await self._start_server():
                return False
            if not await self._init_omni():
                return False
            self._ready = True
            self._playback_task = asyncio.create_task(self._playback_worker())
            logger.info(f"TTS bridge ready: {self._cfg.llama_host}")
            return True
        except Exception as e:
            logger.error(f"start: {e}")
            return False

    async def _start_server(self) -> bool:
        # 1. already running on target port?
        try:
            r = urllib.request.urlopen(f"{self._cfg.llama_host}/health", timeout=2)
            if r.status == 200:
                logger.info(f"llama-server already on :{self._cfg.port}")
                return True
        except Exception:
            pass
        # 2. Comni :19060 available? reuse it
        try:
            r = urllib.request.urlopen("http://localhost:19060/health", timeout=2)
            if r.status == 200:
                logger.info("reusing Comni :19060")
                self._cfg.host = "localhost"
                self._cfg.port = 19060
                return True
        except Exception:
            pass
        # 3. start own
        bin_path = Path(self._cfg.server_bin)
        if not bin_path.exists():
            logger.error(f"binary missing: {bin_path}")
            return False
        model = str(Path(self._cfg.model_dir) / self._cfg.llm_model)
        args = [
            str(bin_path),
            "--host",
            self._cfg.host,
            "--port",
            str(self._cfg.port),
            "--model",
            model,
            "--ctx-size",
            str(self._cfg.ctx_size),
            "--n-gpu-layers",
            str(self._cfg.n_gpu_layers),
        ]
        logger.info(f"starting: {bin_path.name} on :{self._cfg.port}")
        self._proc = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        for _ in range(120):
            await asyncio.sleep(1)
            try:
                urllib.request.urlopen(f"{self._cfg.llama_host}/health", timeout=1)
                logger.info("server ready")
                return True
            except Exception:
                pass
        logger.error("server start timeout")
        return False

    async def _init_omni(self) -> bool:
        url = f"{self._cfg.llama_host}/v1/stream/omni_init"
        data = json.dumps(
            {
                "media_type": 2,
                "use_tts": True,
                "duplex_mode": False,
                "model_dir": self._cfg.model_dir,
                "tts_bin_dir": str(Path(self._cfg.model_dir) / "tts"),
                "tts_gpu_layers": 100,
                "token2wav_device": "gpu:0",
                "output_dir": self._cfg.output_dir,
                "voice_audio": self._cfg.voice_audio,
                "system_prompt": "Speak your response aloud using the reference voice. Always generate speech audio.",
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
            if resp.get("success"):
                self._cnt = 0
                logger.info(f"omni_init OK")
                return True
            if resp.get("voice_audio_used") is not None:
                logger.warning(f"omni_init partial: {resp}")
                self._cnt = 0
                return True
            logger.warning(f"omni_init: {resp}")
            return False
        except Exception as e:
            logger.error(f"omni_init: {e}")
            return False

    def is_ready(self) -> bool:
        return self._ready

    @property
    def llama_host(self) -> str:
        return self._cfg.llama_host

    # ── S2 TTS ─────────────────────────────────────

    async def speak(self, text: str) -> bool:
        if not self._ready or len(text.strip()) < 2:
            return False
        try:
            self._tts_queue.put_nowait(text)
        except asyncio.QueueFull:
            try:
                self._tts_queue.get_nowait()
                self._tts_queue.put_nowait(text)
            except Exception:
                pass
        return True

    async def _playback_worker(self):
        logger.info("TTS playback worker started")
        while self._ready:
            try:
                text = await asyncio.wait_for(self._tts_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                await asyncio.to_thread(self._speak_blocking, text)
            except Exception as e:
                logger.error(f"playback worker: {e}")
            finally:
                self._tts_queue.task_done()
        logger.info("TTS playback worker stopped")

    def _speak_blocking(self, text: str) -> bool:
        # Re-init omni session before each speak to guarantee speech output
        if not self._init_omni_sync():
            return False
        self._cnt += 1
        if not self._prefill(text):
            return False
        wavs = self._decode_and_collect()
        if wavs:
            self._play_wavs(wavs)
            return True
        return False

    def _init_omni_sync(self) -> bool:
        url = f"{self._cfg.llama_host}/v1/stream/omni_init"
        data = json.dumps(
            {
                "media_type": 2,
                "use_tts": True,
                "duplex_mode": False,
                "model_dir": self._cfg.model_dir,
                "tts_bin_dir": str(Path(self._cfg.model_dir) / "tts"),
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
            return resp.get("success", False)
        except Exception as e:
            logger.error(f"reinit omni: {e}")
            return False

    def _prefill(self, text: str) -> bool:
        url = f"{self._cfg.llama_host}/v1/stream/prefill"
        # Prepend speak instruction to trigger TTS
        speak_text = f"Please read the following text aloud in your voice: {text}"
        data = json.dumps({"text": speak_text, "cnt": self._cnt}).encode()
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
        out = Path(self._cfg.output_dir)
        existing = {d.name for d in out.glob("round_*")}
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(
                    url,
                    data=json.dumps({"stream": True}).encode(),
                    headers={"Content-Type": "application/json"},
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
                wavs.extend(sorted((d / "tts_wav").glob("wav_*.wav")))
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
        if self._playback_task:
            self._playback_task.cancel()
            try:
                await self._playback_task
            except asyncio.CancelledError:
                pass
        if self._proc:
            self._proc.terminate()
            self._proc.wait(timeout=10)
        logger.info("bridge stopped")


_bridge: Optional[ComniTTSBridge] = None


async def get_comni_bridge() -> ComniTTSBridge:
    global _bridge
    if _bridge is None:
        _bridge = ComniTTSBridge()
        await _bridge.start()
    return _bridge
