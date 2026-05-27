"""录制器 — 完整直播 .rec 文件记录"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List


@dataclass
class RecordEntry:
    timestamp: float
    event_type: str
    data: Dict[str, Any] = field(default_factory=dict)


class Recorder:
    """录制完整直播会话为 .rec 文件 (JSON Lines)"""

    def __init__(self, output_dir: str = "data/recordings"):
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._session_id = ""
        self._started = False
        self._entry_count = 0

    # ── 生命周期 ───────────────────────────────────────

    def start(self, session_id: str = "") -> str:
        """开始录制, 返回文件路径"""
        self._session_id = session_id or f"stream_{int(time.time())}"
        filename = f"{self._session_id}.rec"
        self._file = (self._dir / filename).open("w", encoding="utf-8")
        self._started = True
        self._entry_count = 0
        # 写入元数据
        self.record("metadata", {"session_id": self._session_id,
                                 "started_at": time.time(), "version": "2.0"})
        return str(self._dir / filename)

    def stop(self) -> int:
        """停止录制, 返回总条目数"""
        if not self._started:
            return 0
        self.record("session_end", {"timestamp": time.time(),
                                    "total_entries": self._entry_count})
        self._started = False
        if self._file:
            self._file.close()
            self._file = None
        return self._entry_count

    # ── 录制接口 ───────────────────────────────────────

    def record(self, event_type: str, data: Dict[str, Any] = None) -> None:
        """录制一条事件"""
        if not self._started or not self._file:
            return
        entry = {
            "t": time.time(),
            "type": event_type,
            "data": data or {},
        }
        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._file.flush()
        self._entry_count += 1

    def record_message(self, user: str, text: str, mentioned: bool = False,
                       is_question: bool = False, msg_id: str = "") -> None:
        self.record("message", {
            "user": user, "text": text,
            "mentioned_bot": mentioned, "is_question": is_question,
            "message_id": msg_id or f"msg_{int(time.time()*1000)}",
        })

    def record_s1_decision(self, token: str, confidence: float = 0,
                           direction: str = "", latency_ms: float = 0) -> None:
        self.record("s1_decision", {
            "token": token, "confidence": confidence,
            "direction": direction, "latency_ms": latency_ms,
        })

    def record_s2_reply(self, content: str, latency_ms: float = 0,
                        thinking_mode: str = "", thinking_len: int = 0) -> None:
        self.record("s2_reply", {
            "content": content, "latency_ms": latency_ms,
            "thinking_mode": thinking_mode, "thinking_len": thinking_len,
        })

    def record_viewer_reaction(self, user: str, text: str,
                               after_reply_ms: float = 0) -> None:
        self.record("viewer_reaction", {
            "user": user, "text": text,
            "after_reply_ms": after_reply_ms,
        })

    # ── 查询 ──────────────────────────────────────────

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def is_recording(self) -> bool:
        return self._started


def load_recording(filepath: str) -> List[RecordEntry]:
    """加载 .rec 文件"""
    entries = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            try:
                data = json.loads(line)
                entries.append(RecordEntry(
                    timestamp=data.get("t", 0),
                    event_type=data.get("type", ""),
                    data=data.get("data", {}),
                ))
            except json.JSONDecodeError:
                pass
    return entries


def extract_interactions(entries: List[RecordEntry]) -> List[Dict]:
    """从录制条目提取互动周期 (trigger → s1 → s2 → reactions)"""
    interactions = []
    current = None

    for e in entries:
        if e.event_type == "message":
            if current and current.get("s2_reply"):
                interactions.append(current)
            current = {"trigger": e.data, "s1_decision": None,
                       "s2_reply": None, "reactions": []}
        elif e.event_type == "s1_decision" and current:
            current["s1_decision"] = e.data
        elif e.event_type == "s2_reply" and current:
            current["s2_reply"] = e.data
        elif e.event_type == "viewer_reaction" and current:
            current["reactions"].append(e.data)

    if current and current.get("s2_reply"):
        interactions.append(current)

    return [ix for ix in interactions if ix.get("s2_reply")]
