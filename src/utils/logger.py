"""分模块日志系统 — 异步写/轮转/JSON+文本双格式"""

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any


class Level(Enum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40

    @property
    def label(self) -> str:
        return {10: "DEBUG", 20: "INFO", 30: "WARN", 40: "ERROR"}[self.value]


class ModuleLogger:
    """单模块日志器 — 异步写入, 自动轮转"""

    def __init__(
        self,
        module: str,
        log_dir: str = "data/logs",
        console: bool = True,
        json_format: bool = True,
        retention_days: int = 30,
    ):
        self.module = module.upper()
        self._log_dir = Path(log_dir)
        self._console = console
        self._json = json_format
        self._retention = retention_days
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self._running = False
        self._writer_task: Optional[asyncio.Task] = None
        self._current_date = ""
        self._text_handle = None
        self._json_handle = None

    async def start(self) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._running = True
        self._rotate_if_needed()
        self._writer_task = asyncio.create_task(self._write_loop())
        self.info("logger_started", log_dir=str(self._log_dir))

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._writer_task:
            self._writer_task.cancel()
            try:
                await self._writer_task
            except (asyncio.CancelledError, RuntimeError):
                pass  # RuntimeError: Queue bound to different event loop
        self._close_handles()

    # ── 日志接口 ───────────────────────────────────────

    def debug(self, msg: str, **extra) -> None:
        self._log(Level.DEBUG, msg, extra)

    def info(self, msg: str, **extra) -> None:
        self._log(Level.INFO, msg, extra)

    def warning(self, msg: str, **extra) -> None:
        self._log(Level.WARNING, msg, extra)

    def error(self, msg: str, **extra) -> None:
        self._log(Level.ERROR, msg, extra)

    def _log(self, level: Level, msg: str, extra: Dict[str, Any]) -> None:
        now = time.time()
        entry = {
            "ts": datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
            "module": self.module,
            "level": level.label,
            "msg": msg,
            "extra": extra,
        }

        if self._console:
            self._print_console(entry)

        if self._running:
            try:
                self._queue.put_nowait(entry)
            except asyncio.QueueFull:
                pass  # 丢弃最旧的, 保留最新的

    # ── 内部 ───────────────────────────────────────────

    def _print_console(self, entry: dict) -> None:
        ts = entry["ts"][11:23]  # HH:MM:SS.mmm
        color = {"DEBUG": "\033[90m", "INFO": "\033[0m", "WARN": "\033[93m", "ERROR": "\033[91m"}
        reset = "\033[0m"
        c = color.get(entry["level"], "")
        print(f"{c}{ts} [{entry['module']:6s}] {entry['level']:5s} {entry['msg']}{reset}")

    async def _write_loop(self) -> None:
        batch = []
        last_flush = time.time()

        try:
            while self._running:
                try:
                    entry = await asyncio.wait_for(self._queue.get(), timeout=2.0)
                    batch.append(entry)
                    self._queue.task_done()
                except asyncio.TimeoutError:
                    pass

                now = time.time()
                if batch and (len(batch) >= 50 or now - last_flush > 2.0):
                    self._rotate_if_needed()
                    self._flush_batch(batch)
                    batch.clear()
                    last_flush = now
        except asyncio.CancelledError:
            pass
        finally:
            # 确保最终flush执行 (即使被cancel)
            if batch:
                self._flush_batch(batch)
            self._close_handles()

    def _flush_batch(self, batch: list) -> None:
        if not batch:
            return
        try:
            if self._text_handle:
                for e in batch:
                    ts = e["ts"][11:23]
                    extra = f" {json.dumps(e['extra'], ensure_ascii=False)}" if e["extra"] else ""
                    self._text_handle.write(
                        f"{ts} [{e['module']:6s}] {e['level']:5s} {e['msg']}{extra}\n"
                    )
                self._text_handle.flush()

            if self._json_handle:
                for e in batch:
                    self._json_handle.write(json.dumps(e, ensure_ascii=False) + "\n")
                self._json_handle.flush()
        except Exception:
            pass  # 磁盘满等极端情况, 不崩溃

    def _rotate_if_needed(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if today == self._current_date and self._text_handle:
            return

        self._close_handles()

        name = self.module.lower()
        self._current_date = today
        text_path = self._log_dir / f"{name}.log"
        json_path = self._log_dir / f"{name}.jsonl"

        # 轮转旧文件 (目标已存在则覆盖, 文件被占用则跳过)
        try:
            if text_path.exists() and text_path.stat().st_size > 0:
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                target = self._log_dir / f"{name}.{yesterday}.log"
                if target.exists():
                    target.unlink()
                text_path.rename(target)
        except (OSError, PermissionError):
            pass  # 文件被占用, 跳过轮转, 直接追加

        try:
            if json_path.exists() and json_path.stat().st_size > 0:
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                target = self._log_dir / f"{name}.{yesterday}.jsonl"
                if target.exists():
                    target.unlink()
                json_path.rename(target)
        except (OSError, PermissionError):
            pass

        self._text_handle = open(text_path, "a", encoding="utf-8")
        self._json_handle = open(json_path, "a", encoding="utf-8")

        # 清理过期日志
        self._prune(self._retention)

    def _close_handles(self) -> None:
        for h in (self._text_handle, self._json_handle):
            if h:
                try:
                    h.close()
                except Exception:
                    pass
        self._text_handle = None
        self._json_handle = None

    def _prune(self, max_days: int) -> int:
        """清理超过max_days天的旧日志, 返回清理数量"""
        cutoff = datetime.now() - timedelta(days=max_days)
        removed = 0
        for f in self._log_dir.glob(f"{self.module.lower()}.*.log"):
            try:
                date_str = f.stem.split(".")[-1]
                if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                    f.unlink()
                    removed += 1
            except (ValueError, OSError):
                pass
        for f in self._log_dir.glob(f"{self.module.lower()}.*.jsonl"):
            try:
                date_str = f.stem.split(".")[-1]
                if datetime.strptime(date_str, "%Y-%m-%d") < cutoff:
                    f.unlink()
                    removed += 1
            except (ValueError, OSError):
                pass
        return removed


class LogManager:
    """全局日志管理器 — 统一管理所有模块日志"""

    def __init__(self, log_dir: str = "data/logs", console: bool = True):
        self._log_dir = log_dir
        self._console = console
        self._loggers: Dict[str, ModuleLogger] = {}

    def get(self, module: str) -> ModuleLogger:
        if module not in self._loggers:
            self._loggers[module] = ModuleLogger(
                module=module,
                log_dir=self._log_dir,
                console=self._console,
            )
        return self._loggers[module]

    async def start_all(self) -> None:
        for lg in self._loggers.values():
            await lg.start()

    async def stop_all(self) -> None:
        for lg in self._loggers.values():
            await lg.stop()

    def get_recent(self, module: str, lines: int = 200) -> list[dict]:
        """读取最近 N 行 JSON 日志 (供 GUI 使用)"""
        json_path = Path(self._log_dir) / f"{module.lower()}.jsonl"
        if not json_path.exists():
            return []
        result = []
        with open(json_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return result[-lines:]


# 全局单例
log_manager = LogManager()
