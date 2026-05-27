"""MiniCPM S1 客户端 — llama.cpp-omni HTTP API"""

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple, List

import aiohttp


@dataclass
class S1RawResponse:
    """MiniCPM 原始响应"""
    content: str
    latency_ms: float
    tokens_generated: int = 0
    error: Optional[str] = None


class MiniCPMClient:
    """
    MiniCPM-o 4.5 推理客户端。

    生产模式: 连接 llama.cpp-omni 的 HTTP API (默认 localhost:9060)
    Mock 模式: 本地返回模拟 Token, 用于无 GPU 环境下的开发和测试
    """

    def __init__(
        self,
        base_url: str = "http://localhost:9060",
        timeout_ms: float = 500.0,
        max_retries: int = 1,
        mock_mode: bool = False,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout_ms = timeout_ms
        self._max_retries = max_retries
        self._mock_mode = mock_mode
        self._session: Optional[aiohttp.ClientSession] = None
        self._mock_responses: List[str] = []
        self._mock_index: int = 0
        self._health: bool = False

    # ── 生命周期 ───────────────────────────────────────

    async def start(self) -> None:
        """初始化 HTTP session"""
        if self._session is not None:
            await self._session.close()
        timeout = aiohttp.ClientTimeout(total=self._timeout_ms / 1000 + 10)
        self._session = aiohttp.ClientSession(timeout=timeout)
        if not self._mock_mode:
            self._health = await self._check_health()
            if not self._health:
                await self._session.close()
                self._session = None
                raise RuntimeError(f"MiniCPM 服务不可达: {self._base_url}")

    async def stop(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
        self._health = False

    # ── 核心接口 ───────────────────────────────────────

    async def decide(
        self,
        system_prompt: str,
        user_context: str,
        temperature: float = 0.1,
        max_tokens: int = 64,
    ) -> S1RawResponse:
        """发送决策请求，返回原始文本"""

        if self._mock_mode:
            return self._mock_decide()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_context},
        ]

        last_error = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._call_api(messages, temperature, max_tokens)
            except asyncio.TimeoutError:
                last_error = f"timeout after {self._timeout_ms}ms"
            except aiohttp.ClientError as e:
                last_error = f"connection error: {e}"
            except Exception as e:
                last_error = f"unexpected: {e}"

            if attempt < self._max_retries:
                await asyncio.sleep(0.05)

        return S1RawResponse(
            content="",
            latency_ms=self._timeout_ms,
            error=last_error or "max retries exceeded",
        )

    async def is_healthy(self) -> bool:
        if self._mock_mode:
            return True
        return await self._check_health()

    # ── Mock 模式 ──────────────────────────────────────

    def set_mock_responses(self, responses: List[str]) -> None:
        """预设 mock 响应序列 (用于测试)"""
        self._mock_responses = list(responses)
        self._mock_index = 0

    def _mock_decide(self) -> S1RawResponse:
        """返回预设的 mock 响应"""
        if self._mock_index < len(self._mock_responses):
            content = self._mock_responses[self._mock_index]
            self._mock_index += 1
        else:
            content = "<|Continue-Listening|>"

        return S1RawResponse(
            content=content,
            latency_ms=12.0,  # mock 延迟
            tokens_generated=len(content) // 3,
        )

    # ── 内部 ───────────────────────────────────────────

    async def _call_api(
        self,
        messages: list,
        temperature: float,
        max_tokens: int,
    ) -> S1RawResponse:
        if not self._session:
            raise RuntimeError("Client not started. Call start() first.")

        t_start = time.perf_counter()

        async with self._session.post(
            f"{self._base_url}/v1/chat/completions",
            json={
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            },
            timeout=aiohttp.ClientTimeout(total=self._timeout_ms / 1000 + 5),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                return S1RawResponse(
                    content="",
                    latency_ms=(time.perf_counter() - t_start) * 1000,
                    error=f"HTTP {resp.status}: {body[:200]}",
                )
            data = await resp.json()

        latency_ms = (time.perf_counter() - t_start) * 1000
        content = data["choices"][0]["message"]["content"].strip()

        return S1RawResponse(
            content=content,
            latency_ms=latency_ms,
            tokens_generated=data.get("usage", {}).get("completion_tokens", 0),
        )

    async def _check_health(self) -> bool:
        try:
            if not self._session:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        f"{self._base_url}/health",
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as r:
                        return r.status == 200
            else:
                async with self._session.get(
                    f"{self._base_url}/health",
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as r:
                    return r.status == 200
        except Exception:
            return False
