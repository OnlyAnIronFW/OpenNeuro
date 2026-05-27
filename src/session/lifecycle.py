"""Session lifecycle tracking"""

import uuid
import time
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: float = 0.0
    ended_at: Optional[float] = None
    recording_path: str = ""
    message_count: int = 0
    reply_count: int = 0
    platform: str = "bilibili"

    def start(self) -> None:
        self.started_at = time.time()

    def end(self) -> None:
        self.ended_at = time.time()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_seconds"] = (
            (self.ended_at - self.started_at)
            if self.ended_at
            else (time.time() - self.started_at)
        )
        return d

    def save(self, path: str = "data/sessions") -> None:
        import os

        os.makedirs(path, exist_ok=True)
        filepath = os.path.join(path, f"{self.session_id}.json")
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
