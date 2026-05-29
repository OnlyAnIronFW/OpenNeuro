"""Separate LLM client for STS2 decisions.

This module is a placeholder for the standalone live_hub.
The STS2 gameplay integration will be rewritten without MaiBot core dependencies.
"""

from __future__ import annotations

from typing import Any, Mapping

import json
import re
from uuid import uuid4

from .sts2_controller import STS2Decision


class STS2DecisionClient:
    """Call a configured OpenAI-compatible model for STS2 action decisions.

    NOTE: This implementation is a no-op placeholder. The STS2 gameplay
    integration is being redesigned for the standalone live_hub and does
    not currently function. Bilibili danmaku capture and distribution are
    unaffected.
    """

    def __init__(self, config: Any, *, logger: Any = None) -> None:
        self.config = config
        self.logger = logger

    async def decide(
        self,
        *,
        state: Mapping[str, Any],
        available_actions: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> STS2Decision:
        raise NotImplementedError(
            "STS2DecisionClient is not yet available in standalone live_hub. "
            "The STS2 gameplay integration is being rewritten."
        )

    def _log_info(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger.info(message)
            except AttributeError:
                pass

    def _log_exception(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger.exception(message)
                return
            except AttributeError:
                pass
            self._log_info(message)


def build_sts2_decision_prompt(
    *,
    state: Mapping[str, Any],
    available_actions: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> str:
    """Build the strict JSON decision prompt."""

    pending_live_replies = _coerce_live_reply_list(state.get("pending_live_replies"))
    payload = {
        "sts2_decision": {
            "game_state": state,
            "available_actions": available_actions,
            "recent_history": history,
        },
        "pending_live_replies": pending_live_replies,
    }
    instructions = (
        "Choose exactly one legal Slay the Spire 2 action from available_actions.\n"
        "Rules:\n"
        "- action must exactly match one available_actions item.\n"
        "- Do not invent unavailable actions, ids, or indexes.\n"
        "- Use card_index only for play_card and only from combat.hand[].index.\n"
        "- Use option_index for every other indexed action, including select_deck_card from selection.cards[].index.\n"
        "- Use target_index only when the selected available action needs a target.\n"
        "- Return strict JSON with exactly these keys: action, card_index, target_index, option_index, reason, narration, expected_result.\n"
        "- Keep reason short and concrete. Do not reveal chain-of-thought.\n"
        "- narration should sound like first-person live commentary for MaiBot.\n"
        "- pending_live_replies contains messages that still need a response.\n"
        "- reply to the pending live messages naturally inside narration while explaining the action.\n"
        "- Super chat suggestions are high priority. Consider them when tactically valid, but never choose an unavailable or bad action just because chat suggested it.\n"
        "- live_chat_context may provide extra viewer context.\n"
        "{sts2_decision}\n"
        "See sts2_decision below.\n"
        "{pending_live_replies}\n"
        "See pending_live_replies below.\n"
    )
    return f"{instructions}{json.dumps(payload, ensure_ascii=False, default=str)}"


def parse_sts2_decision_response(response_text: str) -> STS2Decision:
    """Parse a strict JSON decision response into a normalized decision."""

    payload = _extract_json_object(response_text)
    action = str(payload.get("action") or payload.get("name") or "").strip().lower()
    if not action:
        raise ValueError("STS2 decision response is missing action.")
    reason = str(payload.get("reason") or "").strip()
    narration = str(
        payload.get("narration") or payload.get("commentary") or reason
    ).strip()
    return STS2Decision(
        decision_id=str(payload.get("decision_id") or f"sts2-decision-{uuid4().hex}"),
        action=action,
        card_index=_optional_int(payload.get("card_index")),
        target_index=_optional_int(payload.get("target_index")),
        option_index=_optional_int(payload.get("option_index")),
        reason=reason or action,
        narration=narration or reason or action,
        expected_result=str(payload.get("expected_result") or "").strip(),
        raw=dict(payload),
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    normalized = str(text or "").strip()
    if normalized.startswith("```"):
        normalized = re.sub(
            r"^```(?:json)?", "", normalized.strip(), flags=re.IGNORECASE
        ).strip()
        normalized = re.sub(r"```$", "", normalized).strip()
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        start = normalized.find("{")
        end = normalized.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(normalized[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("STS2 decision response must be a JSON object.")
    return payload


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_live_reply_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]
