"""Subtitle translation client for bilingual Bilibili live replies.

This module is a placeholder for the standalone live_hub.
The subtitle translation feature will be rewritten without MaiBot core dependencies.
"""

from __future__ import annotations

import re
from typing import Any


class SubtitleTranslationClient:
    """Translate English spoken replies into Simplified Chinese subtitles.

    NOTE: This implementation is a no-op placeholder. The translation
    feature is being redesigned for the standalone live_hub and does
    not currently function. Bilibili danmaku capture and distribution are
    unaffected.
    """

    def __init__(self, config: Any, *, logger: Any = None) -> None:
        self.config = config
        self.logger = logger

    async def translate_to_chinese(self, text: str) -> str:
        raise NotImplementedError(
            "SubtitleTranslationClient is not yet available in standalone live_hub. "
            "The translation feature is being rewritten."
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


def _clean_translation_response(text: str) -> str:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```(?:text|markdown|md)?", "", cleaned.strip(), flags=re.IGNORECASE
        ).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _uses_qwen_mt_native_translation(model_identifier: str) -> bool:
    return str(model_identifier or "").strip().lower().startswith("qwen-mt-")
