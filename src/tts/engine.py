"""
MiniCPM-o 4.5 内置 TTS 引擎 — OpenNeuro 语音输出模块

持久的 TTS 引擎实例, 启动时加载模型, 运行时文本→语音。
支持 S1 (MiniCPM) 和 S2 (DeepSeek) 的输出都走 MiniCPM TTS 生成语音。

Usage:
    from src.tts import MiniCPMTTSEngine
    engine = MiniCPMTTSEngine()
    await engine.start()
    audio = await engine.synthesize("こんにちは")
    engine.play(audio)
"""

import asyncio
import json
import logging
import os
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import torch

logger = logging.getLogger("minicpm_tts")


@dataclass
class TTSConfig:
    """TTS 引擎配置."""

    enabled: bool = True
    # 模型路径
    hf_cache_dir: str = os.path.expandvars(
        r"%USERPROFILE%\.cache\huggingface\hub\models--openbmb--MiniCPM-o-4_5\snapshots"
    )
    merged_weights: str = r"F:\OpenNeuro\checkpoints\roxy_tts_v2\merged\checkpoint-final\tts_merged.safetensors"
    # 参考音频 (Roxy 音色 prompt)
    prompt_audio: str = ""  # 留空自动取第一条数据集
    dataset_jsonl: str = r"F:\OpenNeuro\dataset\tts\Roxy_processed\jsonl\dataset.jsonl"
    # 生成参数
    device: str = "cuda"
    sample_rate: int = 16000


class MiniCPMTTSEngine:
    """持久 MiniCPM-o TTS 引擎 — 一次加载, 多次合成."""

    def __init__(self, config: Optional[TTSConfig] = None):
        self._config = config or TTSConfig()
        self._tts = None  # MiniCPMTTS module
        self._s3 = None  # Token2wav (S3 token decoder)
        self._tok = None  # MiniCPMOTokenizerFast
        self._prompt_wav = None  # 缓存参考音频波形
        self._prompt_path = None  # prompt WAV 路径
        self._ready = False
        self._device = None
        self._decode_lock = threading.Lock()

    # ──────────── 初始化 ────────────

    async def start(self) -> bool:
        """加载 TTS 模型和 Token2wav."""
        logger.info("Loading MiniCPM-o TTS engine...")

        try:
            from scripts.train_voice_roxy import (
                TTSFineTuneConfig,
                find_model_cache,
                load_tts_standalone,
                load_s3_tokenizer,
                load_text_tokenizer,
            )
        except ImportError as e:
            logger.error(f"Cannot import training modules: {e}")
            return False

        train_cfg = TTSFineTuneConfig()
        model_dir = find_model_cache(train_cfg, logger)

        # 1. Load TTS
        logger.info("  [1/4] Loading TTS module...")
        self._tts = load_tts_standalone(model_dir, train_cfg, logger)

        # 2. Load trained weights
        logger.info("  [2/4] Loading trained weights...")
        weights_path = Path(self._config.merged_weights)
        if weights_path.exists():
            from safetensors.torch import load_file

            state = load_file(str(weights_path))
            self._tts.load_state_dict(state, strict=False)
            logger.info(f"    Roxy weights: {len(state)} tensors")
        else:
            logger.warning(f"    Trained weights not found: {weights_path}")
            logger.warning("    Using base MiniCPM-o TTS (no voice adaptation)")

        # 3. Load S3 decoder
        logger.info("  [3/4] Loading Token2wav...")
        self._s3 = load_s3_tokenizer(model_dir, logger)

        # 4. Load tokenizer
        logger.info("  [4/4] Loading tokenizer...")
        self._tok = load_text_tokenizer(model_dir, logger)

        # 5. Device
        self._device = torch.device(self._config.device)
        self._tts = self._tts.to(self._device)
        self._tts.eval()

        # 6. Load prompt audio
        self._load_prompt_audio()

        self._ready = True
        param_count = sum(p.numel() for p in self._tts.parameters()) / 1e6
        logger.info(f"TTS engine ready: {param_count:.1f}M params on {self._device}")
        return True

    def _load_prompt_audio(self):
        """加载参考音频作为音色 prompt."""
        import librosa
        import soundfile as sf

        prompt_path = self._config.prompt_audio
        if not prompt_path or not Path(prompt_path).exists():
            # 自动取第一条 Roxy 数据集
            try:
                with open(self._config.dataset_jsonl, encoding="utf-8") as f:
                    first = json.loads(f.readline())
                prompt_path = first["audio"]
            except Exception:
                logger.warning("No reference audio found for TTS prompt")
                return

        self._prompt_path = Path(tempfile.gettempdir()) / "_roxy_tts_prompt.wav"
        wav, sr = librosa.load(prompt_path, sr=16000, mono=True)
        # Trim to 1.5s to fit streaming buffer
        wav = wav[:24000]
        sf.write(str(self._prompt_path), wav, 16000)
        self._prompt_wav = torch.from_numpy(wav).float().unsqueeze(0)
        logger.info(
            f"  Prompt audio: {Path(prompt_path).name} ({len(wav) / 16000:.1f}s)"
        )

    def is_ready(self) -> bool:
        return self._ready

    # ──────────── 合成 ────────────

    async def synthesize(self, text: str) -> Optional[np.ndarray]:
        """文本 → 语音波形。

        NOTE: Standalone MiniCPMTTS.generate() produces garbage S3 tokens
        without LLM conditioning (needs full MiniCPM-o LLM hidden states).
        For now, this returns None — TTS requires the full model pipeline.
        Tracked as TODO: integrate via llama-server speech API.

        Returns:
            numpy array (T,) float32 or None.
        """
        if not self._ready:
            return None
        if not text or len(text.strip()) < 2:
            return None

        # Standalone TTS not yet functional — needs full LLM conditioning
        # TODO: call llama-server /v1/audio/speech endpoint when available
        logger.debug("TTS not available without full LLM conditioning")
        return None

    def _decode_s3(self, s3_tokens: list[int]) -> np.ndarray:
        """S3 tokens → waveform via stream decoder."""
        if not self._prompt_path or not self._prompt_path.exists():
            return np.zeros(self._config.sample_rate)

        with self._decode_lock:
            self._s3.stream_cache, self._s3.hift_cache_dict = self._s3.set_stream_cache(
                str(self._prompt_path)
            )
            wav_out = self._s3.stream(s3_tokens, None, return_waveform=True)
        return wav_out.squeeze()

    # ──────────── 播放 ────────────

    def play(self, audio: np.ndarray, blocking: bool = False):
        """播放音频 (非阻塞, 独立线程)."""
        try:
            import sounddevice as sd

            if blocking:
                sd.play(audio, self._config.sample_rate)
                sd.wait()
            else:
                t = threading.Thread(
                    target=lambda: (
                        sd.play(audio, self._config.sample_rate) or sd.wait()
                    ),
                    daemon=True,
                )
                t.start()
        except ImportError:
            logger.warning(
                "sounddevice not installed. Install: pip install sounddevice"
            )
            # Fallback: save to file
            import hashlib
            import soundfile as sf

            sig = hashlib.md5(audio.tobytes()[:1024]).hexdigest()[:6]
            out = Path(tempfile.gettempdir()) / f"_tts_{sig}.wav"
            sf.write(str(out), audio, self._config.sample_rate)
            logger.info(f"Saved to {out}")

    # ──────────── 清理 ────────────

    async def stop(self):
        self._ready = False
        if self._prompt_path and self._prompt_path.exists():
            self._prompt_path.unlink(missing_ok=True)
        logger.info("TTS engine stopped")


# ── 全局单例 ─────────────────────────────────────────

_engine: Optional[MiniCPMTTSEngine] = None


async def get_tts_engine() -> MiniCPMTTSEngine:
    """获取或创建全局 TTS 引擎单例."""
    global _engine
    if _engine is None:
        _engine = MiniCPMTTSEngine()
        await _engine.start()
    return _engine
