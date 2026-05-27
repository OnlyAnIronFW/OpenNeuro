"""视觉感知 Pipeline — 截屏 + 变化检测 + 事件映射"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, List

import numpy as np


@dataclass
class VisualState:
    """当前画面状态"""

    summary: str = "未知画面"
    changed: bool = False
    is_static: bool = False
    frame_count: int = 0
    last_capture: float = 0.0
    last_change: float = 0.0
    static_duration: float = 0.0
    available: bool = False


class VisualPipeline:
    """
    通用视觉感知 Pipeline。

    流程: 截屏 → 变化检测 → ViT场景描述 → 注入S1/S2
    通用场景: 游戏/桌面/聊天/视频 任何画面都能描述。
    """

    def __init__(
        self,
        fps: int = 2,
        resolution: int = 512,
        change_threshold: float = 0.05,
        use_vit: bool = False,
        vit_api_base: str = "http://localhost:9060",
    ):
        self._fps = fps
        self._resolution = resolution
        self._change_threshold = change_threshold
        self._running = False
        self._prev_frame: Optional[np.ndarray] = None
        self._state = VisualState()
        self._on_change_callbacks: List[Callable] = []
        self._recognizer = None
        if use_vit:
            from .recognizer import VisionRecognizer

            self._recognizer = VisionRecognizer(api_base=vit_api_base)

    # ── 生命周期 ───────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._state.available = True
        if self._recognizer:
            await self._recognizer.start()
        asyncio.create_task(self._capture_loop())

    async def stop(self) -> None:
        self._running = False
        if self._recognizer:
            await self._recognizer.stop()

    # ── 回调 ───────────────────────────────────────────

    def on_change(self, handler):
        """画面变化时回调 handler(scene_description, is_static, changed)"""
        self._on_change_callbacks.append(handler)

    # ── 核心循环 ───────────────────────────────────────

    async def _capture_loop(self) -> None:
        interval = 1.0 / self._fps

        while self._running:
            t_start = time.perf_counter()

            try:
                frame = self._capture_screen()
                changed = self._detect_change(frame)

                if changed:
                    self._prev_frame = frame
                    self._state.last_change = time.time()
                    self._state.static_duration = 0.0
                else:
                    self._state.static_duration += interval

                self._state.changed = changed
                self._state.frame_count += 1
                self._state.last_capture = time.time()

                # 画面变化 → ViT场景描述
                if changed and self._recognizer:
                    try:
                        scene = await self._recognizer.describe_change(
                            frame, self._state.summary
                        )
                        self._state.summary = scene.description
                        self._state.is_static = scene.is_static
                        for cb in self._on_change_callbacks:
                            await cb(scene.description, scene.is_static, changed)
                    except Exception:
                        pass
                elif changed:
                    # 无ViT: 基本检测
                    basic = VisionRecognizer.basic_detect(frame)
                    for cb in self._on_change_callbacks:
                        await cb(basic.description, basic.is_static, changed)

            except Exception:
                self._state.available = False

            elapsed = time.perf_counter() - t_start
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)

    # ── 画面采集 ───────────────────────────────────────

    def _capture_screen(self) -> np.ndarray:
        """截取主屏幕 (降采样到 resolution x resolution)"""
        try:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                # monitors[0] = 全屏, monitors[1] = 主显示器 (如果存在)
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                img = sct.grab(monitor)
                pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
                pil_img = pil_img.resize(
                    (self._resolution, self._resolution), Image.LANCZOS
                )
                return np.array(pil_img)
        except (ImportError, Exception):
            return np.random.randint(
                0, 255, (self._resolution, self._resolution, 3), dtype=np.uint8
            )

    def _detect_change(self, frame: np.ndarray) -> bool:
        """像素差异 > 阈值 → 画面变化"""
        if self._prev_frame is None:
            return True
        diff = np.abs(frame.astype(float) - self._prev_frame.astype(float))
        ratio = np.mean(diff > 30)  # RGB差异>30的像素比例
        return ratio > self._change_threshold

    @property
    def state(self) -> VisualState:
        return self._state

    @property
    def is_alive(self) -> bool:
        return self._running and self._state.available
