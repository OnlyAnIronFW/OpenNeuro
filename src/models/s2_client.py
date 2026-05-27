"""DeepSeek S2 客户端 — OpenAI-compatible API + 3级thinking"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List

import aiohttp


class ThinkingMode(Enum):
    NON_THINK = "non-think"
    THINK_HIGH = "think-high"
    THINK_MAX = "think-max"


@dataclass
class S2Response:
    content: str
    thinking: str = ""
    thinking_mode: ThinkingMode = ThinkingMode.THINK_HIGH
    ttft_ms: float = 0.0
    total_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: Optional[str] = None
    finish_reason: str = "stop"


class DeepSeekClient:
    """
    DeepSeek V4 Flash 客户端。

    特性:
      - 3级 thinking: NON_THINK / THINK_HIGH / THINK_MAX
      - 根据 S1 confidence 自动选择 thinking 模式
      - 超时重试 + 错误降级
      - Mock 模式用于测试
    """

    # thinking 模式 → API 参数 + 推荐 token 预算
    THINKING_CONFIG = {
        ThinkingMode.NON_THINK: {"thinking_type": "disabled"},
        ThinkingMode.THINK_HIGH: {"thinking_type": "enabled", "reasoning_effort": "high"},
        ThinkingMode.THINK_MAX: {"thinking_type": "enabled", "reasoning_effort": "max"},
    }
    MODE_TOKEN_BUDGET = {
        ThinkingMode.NON_THINK: 256,
        ThinkingMode.THINK_HIGH: 512,
        ThinkingMode.THINK_MAX: 768,  # thinking+visible共享, 需预留
    }

    # confidence → thinking 模式
    CONFIDENCE_THRESHOLDS = [
        (0.8, ThinkingMode.THINK_MAX),
        (0.5, ThinkingMode.THINK_HIGH),
    ]

    def __init__(
        self,
        api_key: str = "",
        api_base: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-v4-flash",
        timeout_ms: int = 8000,
        max_retries: int = 2,
        mock_mode: bool = False,
        temperature: float = 1.0,
        top_p: float = 1.0,
    ):
        self._api_key = api_key
        self._api_base = api_base.rstrip("/")
        self._model = model
        self._timeout_ms = timeout_ms
        self._max_retries = max_retries
        self._mock_mode = mock_mode
        self._temperature = temperature
        self._top_p = top_p
        self._session: Optional[aiohttp.ClientSession] = None
        self._mock_responses: List[S2Response] = []
        self._mock_index: int = 0

    # ── 生命周期 ───────────────────────────────────────

    async def start(self) -> None:
        if self._session is not None:
            await self._session.close()
        timeout = aiohttp.ClientTimeout(total=self._timeout_ms / 1000 + 10)
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    # ── 核心接口 ───────────────────────────────────────

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        first_user_message: str = "",
        s1_confidence: float = 0.7,
        max_tokens: int = 512,
    ) -> S2Response:
        """
        生成回复。

        Args:
            system_prompt: S2规则层+人设层
            user_message: 动态上下文
            first_user_message: DeepSeek角色扮演指令 (放在system后第一条)
            s1_confidence: S1置信度 → 自动选thinking模式
            max_tokens: 最大输出token数
        """
        mode = self._select_mode(s1_confidence)

        # token 预算: 按 mode 自动 scale, 保证 thinking+visible 都有空间
        effective_max_tokens = max(max_tokens, self.MODE_TOKEN_BUDGET[mode])

        if self._mock_mode:
            return self._mock_generate(mode)

        messages = [{"role": "system", "content": system_prompt}]
        if first_user_message:
            messages.append({"role": "user", "content": first_user_message})
        messages.append({"role": "user", "content": user_message})

        last_error = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._call_api(messages, mode, effective_max_tokens)
            except asyncio.TimeoutError:
                last_error = f"timeout after {self._timeout_ms}ms"
            except aiohttp.ClientResponseError as e:
                last_error = f"HTTP {e.status}: {e.message[:200]}"
                if 400 <= e.status < 500:
                    break  # 客户端错误不重试
            except aiohttp.ClientError as e:
                last_error = f"connection: {e}"
            except Exception as e:
                last_error = f"unexpected: {e}"

            if attempt < self._max_retries:
                await asyncio.sleep(0.1 * (attempt + 1))

        return S2Response(
            content="",
            thinking_mode=mode,
            error=last_error or "max retries exceeded",
        )

    # ── Mock ───────────────────────────────────────────

    def set_mock_responses(self, responses: List[S2Response]):
        self._mock_responses = list(responses)
        self._mock_index = 0

    def _mock_generate(self, mode: ThinkingMode) -> S2Response:
        if self._mock_index < len(self._mock_responses):
            resp = self._mock_responses[self._mock_index]
            self._mock_index += 1
            resp.thinking_mode = mode
            resp.total_ms = 120.0
            return resp
        return S2Response(
            content="",
            thinking_mode=mode,
            total_ms=5.0,
            error="mock: no more responses",
        )

    # ── 内部 ───────────────────────────────────────────

    def _select_mode(self, confidence: float) -> ThinkingMode:
        for threshold, mode in self.CONFIDENCE_THRESHOLDS:
            if confidence >= threshold:
                return mode
        return ThinkingMode.NON_THINK

    async def _call_api(
        self,
        messages: list,
        mode: ThinkingMode,
        max_tokens: int,
    ) -> S2Response:
        if not self._session:
            raise RuntimeError("Client not started. Call start() first.")

        thinking_cfg = self.THINKING_CONFIG[mode]
        t_start = time.perf_counter()

        async with self._session.post(
            f"{self._api_base}/chat/completions",
            json={
                "model": self._model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": self._temperature,
                "top_p": self._top_p,
                "stream": False,
                **thinking_cfg,
            },
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status,
                    message=body[:300], headers=resp.headers,
                )
            data = await resp.json()

        total_ms = (time.perf_counter() - t_start) * 1000
        choice = data["choices"][0]
        content = (choice["message"].get("content") or "").strip()
        thinking = (choice["message"].get("reasoning_content") or "")

        return S2Response(
            content=content,
            thinking=thinking,
            thinking_mode=mode,
            ttft_ms=0,  # 非流式拿不到精确TTFT
            total_ms=total_ms,
            prompt_tokens=data.get("usage", {}).get("prompt_tokens", 0),
            completion_tokens=data.get("usage", {}).get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
        )
