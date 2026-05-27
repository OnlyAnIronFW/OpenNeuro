"""Phase 3 提炼器 — 从评分中提取决策规则 + Skill"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional

from .scorer import ScoredInteraction


@dataclass
class ExtractedRule:
    rule_type: str  # "s1_decision" | "s2_skill" | "viewer_update"
    description: str
    confidence: float = 0.5
    examples: List[str] = field(default_factory=list)
    source_ids: List[int] = field(default_factory=list)


@dataclass
class ExtractionResult:
    rules: List[ExtractedRule] = field(default_factory=list)
    skills: List[ExtractedRule] = field(default_factory=list)
    viewer_updates: List[Dict] = field(default_factory=list)
    summary: str = ""


class Phase3Extractor:
    """从评分结果提炼可注入的规则和Skill"""

    def extract(self, scores: List[ScoredInteraction]) -> ExtractionResult:
        if not scores:
            return ExtractionResult(summary="无数据")

        result = ExtractionResult()
        n = len(scores)

        # ── S1 决策误判 → 提炼规则 ──
        misjudges = [s for s in scores if s.s1_misjudge]
        if misjudges:
            # 模式: 该说没说 (token包含Continue或为空)
            missed = [s for s in misjudges if not s.s1_token or "Continue" in s.s1_token]
            # 模式: 不该说说了
            overspoke = [s for s in misjudges if "Start" in s.s1_token or "Quick" in s.s1_token]

            if missed:
                triggers = [s.trigger_text[:30] for s in missed[:3]]
                result.rules.append(ExtractedRule(
                    rule_type="s1_decision",
                    description=f"提高回复倾向: 类似\"{'; '.join(triggers)}\"的消息应该回复",
                    confidence=min(0.9, len(missed) / max(n, 1) * 5),
                    examples=triggers,
                ))
            if overspoke:
                triggers = [s.trigger_text[:30] for s in overspoke[:3]]
                result.rules.append(ExtractedRule(
                    rule_type="s1_decision",
                    description=f"降低回复倾向: \"{'; '.join(triggers)}\"类消息可以不回",
                    confidence=min(0.9, len(overspoke) / max(n, 1) * 5),
                    examples=triggers,
                ))

        # ── 高分互动 → 提炼 Skill ──
        high_scores = [s for s in scores
                       if (s.persona_consistency + s.fun_factor + s.timing + s.engagement) / 4 >= 7]
        for s in high_scores[:5]:
            if s.reusable:
                result.skills.append(ExtractedRule(
                    rule_type="s2_skill",
                    description=f"高分回复模式: Q=\"{s.trigger_text[:30]}\" → A=\"{s.reply_text[:40]}\"",
                    confidence=(s.persona_consistency + s.fun_factor + s.timing + s.engagement) / 40,
                    examples=[f"Q: {s.trigger_text[:50]}\nA: {s.reply_text[:80]}"],
                ))

        # ── 人设漂移 → 提炼修复 ──
        drifts = [s for s in scores if s.persona_drift]
        if drifts:
            examples = [s.reply_text[:40] for s in drifts[:3]]
            result.rules.append(ExtractedRule(
                rule_type="s2_skill",
                description=f"修复人设漂移: 以下回复偏离了人设",
                confidence=min(0.9, len(drifts) / max(n, 1) * 3),
                examples=examples,
            ))

        # ── 摘要 ──
        avg = (sum((s.persona_consistency + s.fun_factor + s.timing + s.engagement) / 4
                   for s in scores) / n) if n else 0
        result.summary = (
            f"评分{n}条 | 均分{avg:.1f} | "
            f"误判{len(misjudges)}条 | 漂移{len(drifts)}条 | "
            f"可复用{len(high_scores)}条 | "
            f"规则{len(result.rules)}条 | Skill{len(result.skills)}条"
        )
        return result
