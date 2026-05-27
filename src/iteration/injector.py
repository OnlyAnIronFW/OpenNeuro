"""Phase 4 注入器 — 回放验证 + 人工审批 + 注入上线"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Callable

from .extractor import ExtractionResult, ExtractedRule


@dataclass
class InjectionReport:
    approved: List[ExtractedRule] = field(default_factory=list)
    rejected: List[ExtractedRule] = field(default_factory=list)
    pending: List[ExtractedRule] = field(default_factory=list)
    validation_scores: Dict[str, float] = field(default_factory=dict)
    applied: bool = False
    message: str = ""


class Phase4Injector:
    """注入器 — 回放验证 + 审批 + 应用"""

    def __init__(self, storage_dir: str = "data/memory", prompts=None):
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._history_path = self._dir / "injection_history.json"
        self._history: List[Dict] = self._load_history()
        self._prompts = prompts  # Optional[PromptAssembler]

    # ── 验证 ──────────────────────────────────────────

    def validate(
        self, result: ExtractionResult, replay_validator: Optional[Callable] = None
    ) -> InjectionReport:
        """验证提炼结果"""
        report = InjectionReport()

        for rule in result.rules + result.skills:
            if rule.confidence >= 0.80:
                report.approved.append(rule)
            elif rule.confidence >= 0.50:
                report.pending.append(rule)
            else:
                report.rejected.append(rule)

        # 如果提供了回放验证器, 对 pending 项做验证
        if replay_validator:
            still_pending = []
            for rule in report.pending:
                try:
                    score = replay_validator(rule)
                    report.validation_scores[rule.description[:30]] = score
                    if score >= 0.05:  # 评分提升 >= 5%
                        report.approved.append(rule)
                    else:
                        report.rejected.append(rule)
                except Exception:
                    still_pending.append(rule)
            report.pending = still_pending

        report.message = (
            f"批准{len(report.approved)}条 | "
            f"待批{len(report.pending)}条 | "
            f"拒绝{len(report.rejected)}条"
        )
        return report

    # ── 应用 ──────────────────────────────────────────

    def apply(self, report: InjectionReport, approved_by: str = "auto") -> bool:
        """应用批准的变更到系统"""
        if not report.approved:
            return False

        record = {
            "timestamp": time.time(),
            "approved_by": approved_by,
            "rule_count": len(report.approved),
            "rules": [
                {"type": r.rule_type, "desc": r.description, "conf": r.confidence}
                for r in report.approved
            ],
            "message": report.message,
        }
        self._history.append(record)
        self._save_history()

        # ── Actually apply rules at runtime ────────────
        if self._prompts is not None:
            self._inject_rules(report)

        report.applied = True
        return True

    def _inject_rules(self, report: InjectionReport) -> None:
        """Persist approved rules & reload prompts so S2 picks them up."""
        import os

        skills_dir = Path("data/memory/skills")
        skills_dir.mkdir(parents=True, exist_ok=True)

        for rule in report.approved:
            skill_file = skills_dir / f"skill_{rule.rule_type}.json"
            existing = []
            if skill_file.exists():
                try:
                    existing = json.loads(skill_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            existing.append(
                {
                    "type": rule.rule_type,
                    "desc": rule.description,
                    "conf": rule.confidence,
                }
            )
            skill_file.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # Reload prompt templates so S2 picks up new rules
        self._prompts.reload_all()

    def approve_pending(
        self, rule_indices: List[int], report: InjectionReport
    ) -> InjectionReport:
        """人工审批 pending 项"""
        for idx in rule_indices:
            if 0 <= idx < len(report.pending):
                report.approved.append(report.pending[idx])
        report.pending = [
            r for i, r in enumerate(report.pending) if i not in rule_indices
        ]
        report.message += f" | 人工批准{len(rule_indices)}条"
        return report

    # ── 历史 ──────────────────────────────────────────

    @property
    def history(self) -> List[Dict]:
        return self._history

    def last_injection(self) -> Optional[Dict]:
        return self._history[-1] if self._history else None

    def _load_history(self) -> List[Dict]:
        if self._history_path.exists():
            try:
                return json.loads(self._history_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save_history(self) -> None:
        try:
            self._history_path.write_text(
                json.dumps(self._history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
