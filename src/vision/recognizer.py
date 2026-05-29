"""视觉识别器 — 通用场景语义识别 (MiniCPM ViT)"""

import asyncio
import base64
import io
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

import aiohttp
import numpy as np
from PIL import Image


@dataclass
class SceneResult:
    description: str = ""  # 自然语言场景描述 (10-20字)
    detail: str = ""  # 详细描述 (可选, 供S2使用)
    is_changed: bool = True
    is_static: bool = False
    confidence: float = 0.5
    latency_ms: float = 0.0
    error: Optional[str] = None


class VisionRecognizer:
    """
    通用视觉识别器。

    流程: 截屏 → base64编码 → MiniCPM ViT → 场景描述文本
    输出自然语言描述, 不限定游戏场景 —— 聊天/桌面/浏览器/视频 都能描述。
    """

    SCENE_PROMPT = (
        "Describe what you see on this screen in one short sentence (10-20 Chinese characters). "
        "Be specific about what's happening. "
        "If it's a game: mention what game scene/action. "
        "If it's a desktop: mention what app/window. "
        "If it's a chat: mention what people are talking about. "
        "If it's a video: mention what content. "
        "Reply ONLY the description text, no JSON, no prefixes."
    )

    DETAIL_PROMPT = (
        "Describe this screen in detail (2-3 Chinese sentences). "
        "Include: what app/game, what's happening, any text visible, mood/atmosphere. "
        "Reply ONLY the description, no prefixes."
    )

    def __init__(
        self, api_base: str = "http://localhost:19060", timeout_ms: int = 5000
    ):
        self._api_base = api_base.rstrip("/")
        self._timeout_ms = timeout_ms
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_description: str = ""
        self._static_count: int = 0

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self._timeout_ms / 1000 + 5)
        )

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── 核心接口 ───────────────────────────────────────

    async def recognize(
        self, frame: np.ndarray, *, detailed: bool = False
    ) -> SceneResult:
        """
        识别当前画面, 返回自然语言场景描述。

        Args:
            frame: RGB numpy array (H, W, 3)
            detailed: True=返回更详细的描述 (供S2使用)
        """
        try:
            return await self._call_vit(frame, detailed)
        except Exception as e:
            return SceneResult(
                description="画面不可用",
                is_changed=False,
                error=str(e)[:100],
            )

    async def describe_change(self, frame: np.ndarray) -> SceneResult:
        """
        检测画面变化并描述。如果画面没变, 返回 is_changed=False。
        """
        result = await self.recognize(frame)
        if result.error:
            return result

        # 与上次描述比较
        if result.description == self._last_description:
            self._static_count += 1
            result.is_changed = False
            result.is_static = self._static_count > 30
        else:
            self._static_count = 0
            result.is_changed = True

        self._last_description = result.description
        return result

    # ── ViT 调用 ──────────────────────────────────────

    async def _call_vit(self, frame: np.ndarray, detailed: bool) -> SceneResult:
        if not self._session:
            return SceneResult(error="session not started")

        # base64 JPEG
        img = Image.fromarray(frame.astype(np.uint8))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=50)
        b64 = base64.b64encode(buf.getvalue()).decode()
        prompt = self.DETAIL_PROMPT if detailed else self.SCENE_PROMPT

        t0 = time.perf_counter()
        async with self._session.post(
            f"{self._api_base}/v1/chat/completions",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                "max_tokens": 128,
                "temperature": 0.1,
                "stream": False,
            },
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                return SceneResult(
                    error=f"HTTP {resp.status}: {body[:100]}",
                    latency_ms=(time.perf_counter() - t0) * 1000,
                )
            data = await resp.json()

        latency_ms = (time.perf_counter() - t0) * 1000
        content = data["choices"][0]["message"]["content"].strip()

        return SceneResult(
            description=content[:30],
            detail=content if detailed else "",
            confidence=0.7,
            latency_ms=latency_ms,
        )

    # ── 纯文本 fallback (无 ViT 时) ────────────────────

    @staticmethod
    def basic_detect(frame: np.ndarray) -> SceneResult:
        """无 ViT 时的基本检测: 判断画面是否变化/静止/黑屏"""
        mean_brightness = np.mean(frame)
        std_brightness = np.std(frame)

        if mean_brightness < 20:
            return SceneResult(description="黑屏/加载中", is_changed=False)
        if std_brightness < 10:
            return SceneResult(description="静态画面", is_static=True)
        return SceneResult(description="画面有变化")

    # ── S2 Prompt 注入 ────────────────────────────────

    def build_visual_context(self, result: SceneResult) -> str:
        """将场景识别结果转为 S2 prompt 片段"""
        if result.error:
            return ""
        parts = [f"画面: {result.description}"]
        if result.detail:
            parts.append(result.detail)
        if result.is_static:
            parts.append("(画面已静止超过30秒)")
        return "\n".join(parts)
