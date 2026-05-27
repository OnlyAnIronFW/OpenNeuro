"""Phase 2 评分器 — 离线批量评分互动质量"""

import json
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from src.models.s2_client import DeepSeekClient, S2Response


@dataclass
class ScoredInteraction:
    trigger_text: str
    reply_text: str
    s1_token: str = ""
    persona_consistency: float = 5.0
    fun_factor: float = 5.0
    timing: float = 5.0
    engagement: float = 5.0
    s1_misjudge: bool = False
    persona_drift: bool = False
    reusable: bool = False


class Phase2Scorer:
    """离线批量评分互动 — 用 DeepSeek 做评审"""

    SCORING_PROMPT = """你是AI主播互动质量评审。对以下互动评分(0-10):

评分维度:
- persona_consistency: 回复是否符合人设
- fun_factor: 有趣程度/观众会不会笑
- timing: 回复时机对不对
- engagement: 能不能引发更多互动

额外标记:
- s1_misjudge: S1决策明显错误 (该说不说/不该说说了)
- persona_drift: 回复明显偏离人设
- reusable: 是否值得做成可复用的Skill

输出纯JSON数组:
[{"id":1,"persona_consistency":8,"fun_factor":7,"timing":9,"engagement":6,"s1_misjudge":false,"persona_drift":false,"reusable":false}]"""

    def __init__(
        self, s2_client: Optional[DeepSeekClient] = None, use_self_eval: bool = False
    ):
        self._s2 = s2_client
        self._use_self_eval = use_self_eval

    async def score_batch(
        self, interactions: List[Dict], batch_size: int = 15
    ) -> List[ScoredInteraction]:
        """批量评分互动"""
        results = []
        for i in range(0, len(interactions), batch_size):
            batch = interactions[i : i + batch_size]
            scores = await self._score_one_batch(batch)
            results.extend(scores)
        return results

    async def _score_one_batch(self, batch: List[Dict]) -> List[ScoredInteraction]:
        if self._use_self_eval:
            # ── DeepSeek 评分路径（实验性，存在自评偏差）──
            items = []
            for idx, ix in enumerate(batch):
                trigger = (ix.get("trigger") or {}).get("text", "")
                reply = (ix.get("s2_reply") or {}).get("content", "")
                s1 = (ix.get("s1_decision") or {}).get("token", "")
                reactions = [r.get("text", "") for r in (ix.get("reactions") or [])]
                items.append(
                    f'互动{idx + 1}: 触发="{trigger[:40]}" '
                    f'S1={s1} AI回复="{reply[:60]}" '
                    f"观众反应={reactions[:3]}"
                )

            prompt = f"{self.SCORING_PROMPT}\n\n" + "\n".join(items)

            resp = await self._s2.generate(
                system_prompt="你是互动质量评审器。严格按JSON格式输出。",
                user_message=prompt,
                first_user_message="用中文回答。严格输出JSON。",
                s1_confidence=0.5,
                max_tokens=2000,
            )

            return self._parse_scores(resp.content, batch)

        # ── 启发式基线评分（无自评偏差）──
        results = []
        for idx, ix in enumerate(batch):
            reply = (ix.get("s2_reply") or {}).get("content", "")
            heuristic_score = self._score_heuristic(reply, idx, ix)
            # Map 0-8 heuristic → 0-10 dimension scale
            dim_score = heuristic_score * 10.0 / 8.0
            results.append(
                ScoredInteraction(
                    trigger_text=(ix.get("trigger") or {}).get("text", ""),
                    reply_text=reply,
                    s1_token=(ix.get("s1_decision") or {}).get("token", ""),
                    persona_consistency=dim_score,
                    fun_factor=dim_score,
                    timing=dim_score,
                    engagement=dim_score,
                    s1_misjudge=False,
                    persona_drift=False,
                    reusable=False,
                )
            )
        return results

    def _parse_scores(
        self, response: str, batch: List[Dict]
    ) -> List[ScoredInteraction]:
        try:
            data = json.loads(self._extract_json(response))
            if not isinstance(data, list):
                return []
        except (json.JSONDecodeError, TypeError):
            return []

        results = []
        for item in data:
            if not isinstance(item, dict):
                continue
            idx = item.get("id", 1) - 1  # id从1开始, 缺失默认为1
            if 0 <= idx < len(batch):
                ix = batch[idx]
                results.append(
                    ScoredInteraction(
                        trigger_text=(ix.get("trigger") or {}).get("text", ""),
                        reply_text=(ix.get("s2_reply") or {}).get("content", ""),
                        s1_token=(ix.get("s1_decision") or {}).get("token", ""),
                        persona_consistency=float(item.get("persona_consistency", 5)),
                        fun_factor=float(item.get("fun_factor", 5)),
                        timing=float(item.get("timing", 5)),
                        engagement=float(item.get("engagement", 5)),
                        s1_misjudge=bool(item.get("s1_misjudge", False)),
                        persona_drift=bool(item.get("persona_drift", False)),
                        reusable=bool(item.get("reusable", False)),
                    )
                )
        return results

    @staticmethod
    def _extract_json(text: str) -> str:
        m = re.search(r"\[[\s\S]*\]", text)
        return m.group(0) if m else text

    def _score_heuristic(self, reply: str, reply_idx: int, obs: dict) -> float:
        score = 0.0
        # persona_consistency (2pts): check for forbidden patterns
        forbidden = ["作为AI", "我是一个", "无法回答", "对不起"]
        hits = sum(1 for f in forbidden if f in reply)
        score += max(0, 2.0 - hits * 0.5)

        # fun_factor (3pts): length + emoji + punctuation variety
        emoji_count = sum(1 for c in reply if ord(c) > 0x1F600)
        punct_variety = len(set(c for c in reply if c in "?!~…"))
        length_score = min(3.0, len(reply) / 50.0)
        score += length_score * 0.5 + emoji_count * 0.3 + punct_variety * 0.2

        # engagement (2pts): questions + call-to-action
        question_count = reply.count("?") + reply.count("？")
        cta_patterns = ["快来", "一起", "试试", "点个", "关注"]
        cta_count = sum(1 for p in cta_patterns if p in reply)
        score += min(2.0, question_count * 0.5 + cta_count * 0.5)

        # timing (1pt): no real timing data, give baseline
        score += 1.0

        return min(8.0, score)  # cap at 8

    def summarize(self, scores: List[ScoredInteraction]) -> Dict:
        """生成评分摘要"""
        if not scores:
            return {
                "scoring_method": "self_eval"
                if self._use_self_eval
                else "heuristic_baseline",
                "total": 0,
            }
        n = len(scores)
        return {
            "scoring_method": "self_eval"
            if self._use_self_eval
            else "heuristic_baseline",
            "total": n,
            "avg_persona": sum(s.persona_consistency for s in scores) / n,
            "avg_fun": sum(s.fun_factor for s in scores) / n,
            "avg_timing": sum(s.timing for s in scores) / n,
            "avg_engagement": sum(s.engagement for s in scores) / n,
            "misjudge_count": sum(1 for s in scores if s.s1_misjudge),
            "drift_count": sum(1 for s in scores if s.persona_drift),
            "reusable_count": sum(1 for s in scores if s.reusable),
            "high_score_count": sum(
                1
                for s in scores
                if (s.persona_consistency + s.fun_factor + s.timing + s.engagement) / 4
                >= 7
            ),
            "low_score_count": sum(
                1
                for s in scores
                if (s.persona_consistency + s.fun_factor + s.timing + s.engagement) / 4
                < 4
            ),
        }
