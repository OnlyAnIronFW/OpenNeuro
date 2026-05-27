"""Subtitle translation client for bilingual Bilibili live replies."""

from __future__ import annotations

import re
from typing import Any

from openai import AsyncOpenAI

from src.config.config import config_manager
from src.config.model_configs import APIProvider, ModelInfo
from src.llm_models.openai_compat import build_openai_compatible_client_config, split_openai_request_overrides

from .config import SubtitleTranslationConfig


class SubtitleTranslationClient:
    """Translate English spoken replies into Simplified Chinese subtitles."""

    def __init__(self, config: SubtitleTranslationConfig, *, logger: Any = None) -> None:
        self.config = config
        self.logger = logger

    async def translate_to_chinese(self, text: str) -> str:
        source_text = str(text or "").strip()
        if not source_text:
            return ""
        provider, model_identifier, model_extra_params = self._resolve_provider_and_model()
        self._log_info(
            "Subtitle translation LLM resolved: "
            f"provider={getattr(provider, 'name', self.config.api_provider)!r} model={model_identifier!r} "
            f"enable_thinking={bool(self.config.enable_thinking)}"
        )
        client_config = build_openai_compatible_client_config(provider)
        request_overrides = split_openai_request_overrides(
            {
                **model_extra_params,
                "enable_thinking": bool(self.config.enable_thinking),
            }
        )
        client = AsyncOpenAI(
            api_key=client_config.api_key,
            base_url=client_config.base_url,
            timeout=self.config.timeout_sec,
            max_retries=provider.max_retry,
            default_headers=client_config.default_headers or None,
            default_query=client_config.default_query or None,
        )
        try:
            if _uses_qwen_mt_native_translation(model_identifier):
                translation_options = dict(request_overrides.extra_body.get("translation_options") or {})
                translation_options.setdefault("source_lang", "English")
                translation_options.setdefault("target_lang", "Chinese")
                extra_body = dict(request_overrides.extra_body)
                extra_body.pop("enable_thinking", None)
                extra_body["translation_options"] = translation_options
                response = await client.chat.completions.create(
                    model=model_identifier,
                    messages=[{"role": "user", "content": source_text}],
                    extra_headers=request_overrides.extra_headers or None,
                    extra_query=request_overrides.extra_query or None,
                    extra_body=extra_body or None,
                )
            else:
                response = await client.chat.completions.create(
                    model=model_identifier,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Translate English live-stream captions into natural Simplified Chinese subtitles. "
                                "Keep names, memes, and technical terms concise. Return only the Chinese subtitle text; "
                                "do not include quotes, notes, markdown, or explanations."
                            ),
                        },
                        {"role": "user", "content": source_text},
                    ],
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                    extra_headers=request_overrides.extra_headers or None,
                    extra_query=request_overrides.extra_query or None,
                    extra_body=request_overrides.extra_body or None,
                )
            content = response.choices[0].message.content if response.choices else ""
            translated = _clean_translation_response(str(content or ""))
            if translated:
                self._log_info("Subtitle translation completed.")
            return translated
        except Exception:
            self._log_exception("Subtitle translation LLM request failed")
            raise

    def _resolve_provider_and_model(self) -> tuple[APIProvider, str, dict[str, Any]]:
        model_config = config_manager.get_model_config()
        models_by_name = {model.name: model for model in model_config.models}
        providers_by_name = {provider.name: provider for provider in model_config.api_providers}

        model_info: ModelInfo | None = None
        if self.config.model_name:
            model_info = models_by_name.get(self.config.model_name)
            if model_info is None:
                raise RuntimeError(f"Subtitle translation model_name not found in model_config: {self.config.model_name}")

        if model_info is not None:
            provider = providers_by_name.get(model_info.api_provider)
            if provider is None:
                raise RuntimeError(f"Subtitle translation provider not found in model_config: {model_info.api_provider}")
            return provider, model_info.model_identifier, dict(model_info.extra_params or {})

        provider = providers_by_name.get(self.config.api_provider)
        if provider is None:
            raise RuntimeError(f"Subtitle translation api_provider not found in model_config: {self.config.api_provider}")
        model_identifier = self.config.model_identifier.strip()
        if not model_identifier:
            raise RuntimeError("Subtitle translation model_identifier is empty.")
        return provider, model_identifier, {}

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
        cleaned = re.sub(r"^```(?:text|markdown|md)?", "", cleaned.strip(), flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _uses_qwen_mt_native_translation(model_identifier: str) -> bool:
    return str(model_identifier or "").strip().lower().startswith("qwen-mt-")
