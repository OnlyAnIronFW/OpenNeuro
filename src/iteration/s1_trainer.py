"""Phase 6: S1 微调数据管线 — 收集+导出+评估"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any, List


@dataclass
class S1TrainingSample:
    """一条 S1 训练样本"""
    # 输入: S1 看到的上下文
    messages_json: str          # 最近消息的 JSON
    visual_summary: str = ""
    thread_snapshot: str = ""   # 线程快照 JSON
    working_memory: str = ""    # 工作记忆 JSON

    # S1 原始输出
    s1_raw_output: str = ""
    s1_token: str = ""
    s1_confidence: float = 0.0

    # 人类修正 (如果 S1 判断错了)
    human_correction_token: str = ""
    human_correction_reason: str = ""

    # 元数据
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""
    is_misjudged: bool = False


class S1TrainingCollector:
    """
    S1 训练数据收集器。

    用法: 在 handle_message 中, 每次 S1 决策后调用 record()
    人工通过 GUI 标记错误决策 → 导出为微调格式
    """

    def __init__(self, output_dir: str = "data/training"):
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._samples: List[S1TrainingSample] = []
        self._session_id: str = ""
        self._stats = {"total": 0, "corrected": 0, "pending": 0}

    # ── 收集 ───────────────────────────────────────────

    def start_session(self, session_id: str = "") -> None:
        self._session_id = session_id or f"train_{int(time.time())}"

    def record(
        self,
        messages: List[Dict],
        s1_raw: str,
        s1_token: str,
        s1_confidence: float = 0.0,
        visual_summary: str = "",
        thread_snapshot: List[Dict] = None,
        working_memory: Dict = None,
    ) -> S1TrainingSample:
        """记录一次 S1 决策"""
        sample = S1TrainingSample(
            messages_json=json.dumps(messages[-5:], ensure_ascii=False),
            visual_summary=visual_summary,
            thread_snapshot=json.dumps((thread_snapshot or [])[:5], ensure_ascii=False),
            working_memory=json.dumps(working_memory or {}, ensure_ascii=False),
            s1_raw_output=s1_raw,
            s1_token=s1_token,
            s1_confidence=s1_confidence,
            session_id=self._session_id,
        )
        self._samples.append(sample)
        self._stats["total"] += 1
        self._stats["pending"] += 1
        return sample

    def mark_correction(self, sample_idx: int, correct_token: str,
                        reason: str = "") -> None:
        """标记人工修正"""
        if 0 <= sample_idx < len(self._samples):
            s = self._samples[sample_idx]
            s.human_correction_token = correct_token
            s.human_correction_reason = reason
            s.is_misjudged = (correct_token != s.s1_token)
            if s.is_misjudged:
                self._stats["corrected"] += 1
            self._stats["pending"] = max(0, self._stats["pending"] - 1)

    # ── 导出 ───────────────────────────────────────────

    def export_alpaca(self, output_path: str = "") -> str:
        """
        导出为 Alpaca 微调格式:
        {
          "instruction": "你是AI主播的S1决策引擎...",
          "input": "【新消息】...",
          "output": "<|Start-Speaking confidence=0.8|> ..."
        }
        """
        data = []
        for s in self._samples:
            token = s.human_correction_token or s.s1_token
            # 跳过未标记的 pending 样本
            if not s.human_correction_token and s.is_misjudged is None:
                continue

            instruction = (
                "你是AI主播的S1实时决策引擎。根据聊天上下文, "
                "输出决策Token: <|Start-Speaking confidence=N|> 回复方向, "
                "或 <|Quick-Reply|> 简短文本, 或 <|Continue-Listening|>。"
            )
            inp = f"【新消息】\n{s.messages_json}\n【线程】\n{s.thread_snapshot}"
            output = s.s1_raw_output if token == s.s1_token else (
                f"<|{token} confidence={s.s1_confidence:.2f}|> {s.human_correction_reason}"
            )

            data.append({
                "instruction": instruction,
                "input": inp,
                "output": output,
            })

        path = output_path or str(self._dir / f"s1_alpaca_{self._session_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def export_sharegpt(self, output_path: str = "") -> str:
        """导出为 ShareGPT 格式 (兼容 unsloth)"""
        data = []
        for s in self._samples:
            token = s.human_correction_token or s.s1_token
            if not s.human_correction_token:
                continue  # 只导出已标记的

            data.append({
                "conversations": [
                    {"from": "human", "value": (
                        f"【系统】你是AI主播S1决策引擎。\n"
                        f"【消息】{s.messages_json}\n"
                        f"【画面】{s.visual_summary}\n"
                        f"【线程】{s.thread_snapshot}"
                    )},
                    {"from": "gpt", "value": (
                        f"<|{token} confidence={s.s1_confidence:.2f}|> "
                        f"{s.human_correction_reason}"
                    )},
                ]
            })

        path = output_path or str(self._dir / f"s1_sharegpt_{self._session_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def export_jsonl(self, output_path: str = "") -> str:
        """导出为 JSONL (通用格式)"""
        path = output_path or str(self._dir / f"s1_samples_{self._session_id}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for s in self._samples:
                f.write(json.dumps({
                    "messages": s.messages_json,
                    "visual": s.visual_summary,
                    "threads": s.thread_snapshot,
                    "s1_raw": s.s1_raw_output,
                    "s1_token": s.s1_token,
                    "correction_token": s.human_correction_token,
                    "correction_reason": s.human_correction_reason,
                    "is_misjudged": s.is_misjudged,
                }, ensure_ascii=False) + "\n")
        return path

    # ── 统计 ───────────────────────────────────────────

    @property
    def stats(self) -> Dict:
        return dict(self._stats)

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def get_misjudged(self) -> List[S1TrainingSample]:
        return [s for s in self._samples if s.is_misjudged]

    def get_corrected(self) -> List[S1TrainingSample]:
        return [s for s in self._samples if s.human_correction_token]

    def clear(self) -> None:
        self._samples.clear()
        self._stats = {"total": 0, "corrected": 0, "pending": 0}
