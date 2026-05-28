"""LLM-driven expression generator — SoulLink-style JSON parameter control.

Sends the complete 52-parameter BlendShape list (plus VAD and text context)
to a configurable LLM backend, which returns a JSON dict of all BlendShape
values. This is the REMOTE path — high quality, context-aware, but latently
dependent on API availability.

Architecture (SoulLink reference):
    In SoulLink Live2D, the LLM receives the full parameter list and must
    respond with values for ALL parameters. This "mandatory all-parameter"
    output pattern prevents the LLM from cherry-picking only a few sliders
    and leaving the rest at stale values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from .profile_loader import AvatarProfile


@dataclass
class LLMConfig:
    """Configuration for the expression-generation LLM backend.

    Attributes:
        api_base: Base URL of the LLM API endpoint (OpenAI-compatible).
        api_key: Authentication key for the API.
        model: Model name (e.g. "deepseek-chat", "gpt-4o-mini").
        temperature: Sampling temperature; recommended 0.1–0.3 for
            parameter consistency between calls.
        max_tokens: Maximum response tokens.
        timeout_ms: Request timeout in milliseconds.
        custom_system_prompt: Override the default system prompt with
            a per-avatar instruction (loaded from ``avatar_prompt.txt``
            or ``.vrm_profile.json``).
    """

    api_base: str = ""
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout_ms: int = 8000
    custom_system_prompt: Optional[str] = None


class ExpressionGenerator:
    """Generate 52-channel BlendShape parameters via LLM inference.

    The generator takes a text utterance (what the avatar is about to say),
    an optional context string (recent dialogue, emotional state description),
    and produces a complete ``dict[str, float]`` mapping every ARKit
    BlendShape name to a value in [0.0, 1.0].

    Design Decisions (SoulLink Architecture):
        **Mandatory All-Parameter Output**
        The system prompt explicitly instructs the LLM to include ALL 52
        BlendShape keys in the JSON output. Missing keys would leave the
        avatar with stale facial parameters from the previous expression,
        creating visual glitches. The LLM must reason over the full set.

        **System Prompt Design**
        The default system prompt follows this structure:
        1. Role: "你是VRM虚拟形象的表情控制器"
        2. Available BlendShapes: full 52-name list with descriptions
        3. Diversity requirement: vary expressions to avoid monotony
        4. Output format: strict JSON with all keys
        5. Value rules: ranges, smooth transitions, facial plausibility

        **Per-Avatar Custom Prompts**
        Each avatar can bundle an ``avatar_prompt.txt`` that overrides
        the default system prompt, allowing per-character expression
        style (e.g. a cool character never uses MouthSmileLeft).
        Pattern matches SoulLink's ``model_prompt.txt``.

        **Temperature Recommendation**
        0.1–0.3 is optimal. Below 0.1 produces near-identical expressions
        across different inputs (robotic). Above 0.3 causes parameter
        jitter between frames (flickering expressions).

        **Concurrency**
        Chat generation and expression generation run concurrently
        via ``asyncio.gather``: the avatar speaks AND emotes at the same
        time, reducing perceived latency.

    Usage::

        gen = ExpressionGenerator(profile, LLMConfig(api_key="..."))
        blendshapes = await gen.generate("你太厉害了！", context="收到夸奖")
        # blendshapes = {"MouthSmileLeft": 0.8, "EyeSquintLeft": 0.6, ...}
    """

    def __init__(self, profile: "AvatarProfile", llm_config: LLMConfig) -> None:
        """Initialize the expression generator.

        Args:
            profile: An ``AvatarProfile`` dataclass containing the avatar's
                BlendShape map, custom prompt path, and physics params.
            llm_config: LLM backend configuration (endpoint, model, temperature).
        """
        self._profile = profile
        self._config = llm_config
        self._session_prompt: Optional[str] = None

    async def generate(self, text: str, context: str = "") -> Dict[str, float]:
        """Generate BlendShape parameters for a text utterance.

        Sends the text (and optional context) to the LLM with the
        system prompt containing all 52 BlendShape keys. The LLM
        returns a JSON object that is validated against the known
        BlendShape names.

        Args:
            text: The text the avatar is about to speak (SSML-cleaned).
            context: Optional contextual description of the emotional
                situation, recent events, or dialogue history. Passed
                as part of the user message to guide expression choice.

        Returns:
            Dictionary mapping each ARKit BlendShape name to a float
            value in [0.0, 1.0]. Stub raises NotImplementedError.

        Raises:
            NotImplementedError: This is a stub — implementation pending.
        """
        raise NotImplementedError(
            "ExpressionGenerator.generate() is not yet implemented. "
            "Planned: send text + context to LLM with full BlendShape system prompt, "
            "parse JSON response, validate against known BlendShape keys."
        )

    def _build_system_prompt(self) -> str:
        """Build the system prompt for expression generation.

        The prompt includes:
        1. Role description in Chinese
        2. Full list of 52 ARKit BlendShape names with value ranges
        3. Output format requirements (strict JSON)
        4. Diversity and naturalness guidelines
        5. Per-avatar custom instructions override

        Returns:
            Complete system prompt string.

        Raises:
            NotImplementedError: This is a stub.
        """
        raise NotImplementedError(
            "ExpressionGenerator._build_system_prompt() is not yet implemented."
        )

    def _validate_response(self, raw: Dict[str, Any]) -> Dict[str, float]:
        """Validate and sanitize the LLM's BlendShape response.

        Checks:
        - All 52 expected BlendShape keys are present
        - All values are floats in [0.0, 1.0]
        - No extra unknown keys (ignored with warning)
        - Head transform keys (HeadYaw, HeadPitch, HeadRoll) clamped
          to VRM-valid ranges

        Args:
            raw: Raw JSON dict from the LLM response.

        Returns:
            Cleaned dict with validated float values.

        Raises:
            NotImplementedError: This is a stub.
        """
        raise NotImplementedError(
            "ExpressionGenerator._validate_response() is not yet implemented."
        )
