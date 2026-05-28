"""TTS-synchronized motion planning — SoulLink two-phase LLM pipeline.

For text-to-speech playback, we need BlendShape + joint-motion keyframes
synchronized to the audio timeline. The SoulLink architecture uses a two-phase
LLM pipeline: first plan abstract action descriptions, then convert each
description into concrete BlendShape parameters.

Two-Phase Architecture:
    ┌──────────────────────────────────────────────────────────┐
    │  Phase 1: Action Planning                                │
    │  TTS text → LLM → list of action descriptions per 2s     │
    │  "微笑并轻轻侧头" / "瞪大眼睛表示惊讶"                    │
    │                                                          │
    │  Phase 2: Parameter Generation (parallel via asyncio)     │
    │  Each description → LLM → BlendShape parameters          │
    │  Mouth params FILTERED (handled by lip-sync separately)  │
    └──────────────────────────────────────────────────────────┘
"""

from typing import Dict, List, Optional


class MotionPlanner:
    """Plan and generate TTS-synchronized motion keyframes.

    The planner receives the full speech text and the total number of
    animation frames (derived from TTS audio duration). It splits the
    timeline into ~2-second segments, generates action descriptions
    for each segment, and then converts each description into a set
    of BlendShape parameter keyframes.

    Design Decisions:
        **Two-Phase LLM Pipeline**
        Phase 1 produces human-readable action descriptions
        (e.g. "微笑并轻轻侧头"). Phase 2 converts each description
        into precise BlendShape values. This split improves output
        quality because the LLM writes descriptions holistically
        first, then parameterizes them in a second focused pass.

        **Parallel Frame Generation**
        Phase 2 runs ALL frame conversions concurrently via
        ``asyncio.gather``, because each frame's parameterization
        is independent of others.

        **Mouth Parameter Filtering**
        Mouth-related BlendShape keys (JawOpen, MouthSmile*, etc.)
        are set to 0.0 in the LLM output and replaced by the lip-sync
        system at runtime. The lip-sync tracker computes these from
        the audio waveform, which is far more accurate than LLM guesses.

        **Joint Motion Boost**
        Head/body joint parameters (HeadYaw, HeadPitch, HeadRoll)
        can receive a configurable multiplier to amplify subtle
        movements for more visible avatar animation.

    Usage::

        planner = MotionPlanner(llm_config)
        frames = await planner.plan_motion("大家好欢迎来到直播间！", total_frames=240)
        for frame in frames:
            bs = await planner.generate_frame(frame)
            # bs = {"HeadYaw": 5.2, "MouthSmileLeft": 0.7, ...}
    """

    # BlendShape keys that are handled by lip-sync (not by LLM)
    MOUTH_KEYS: List[str] = [
        "JawOpen",
        "MouthClose",
        "MouthFunnel",
        "MouthPucker",
        "MouthLeft",
        "MouthRight",
        "MouthSmileLeft",
        "MouthSmileRight",
        "MouthFrownLeft",
        "MouthFrownRight",
        "MouthDimpleLeft",
        "MouthDimpleRight",
        "MouthStretchLeft",
        "MouthStretchRight",
        "MouthRollLower",
        "MouthRollUpper",
        "MouthShrugLower",
        "MouthShrugUpper",
        "MouthPressLeft",
        "MouthPressRight",
        "MouthLowerDownLeft",
        "MouthLowerDownRight",
        "MouthUpperUpLeft",
        "MouthUpperUpRight",
        "JawForward",
        "JawLeft",
        "JawRight",
    ]

    def __init__(self, llm_config: Optional[object] = None) -> None:
        """Initialize the motion planner.

        Args:
            llm_config: LLM backend configuration for generating
                action descriptions and parameterizing them. Same
                config type as ``ExpressionGenerator.LLMConfig``.
        """
        self._config = llm_config
        self._segment_duration_ms: int = 2000  # ~2 seconds per segment

    async def plan_motion(self, speech_text: str, total_frames: int) -> List[Dict]:
        """Phase 1: Plan action descriptions for the speech timeline.

        Divides the total frame count into ~2-second segments.
        For each segment, asks the LLM to produce a natural-language
        action description that matches the speech content at that
        point in time.

        Args:
            speech_text: The full TTS text that will be spoken.
            total_frames: Total number of animation frames at the
                target frame rate (typically 30fps or 60fps).

        Returns:
            A list of frame-plan dicts, each containing::

                {
                    "start_frame": int,      # inclusive start frame index
                    "end_frame": int,        # exclusive end frame index
                    "description": str,      # eg. "微笑并轻轻侧头"
                    "duration_ms": int,      # segment duration in ms
                }

        Raises:
            NotImplementedError: This is a stub — implementation pending.
        """
        raise NotImplementedError(
            "MotionPlanner.plan_motion() is not yet implemented. "
            "Planned: split speech into ~2s segments, send each with text to LLM, "
            "collect action descriptions as frame-plan dicts."
        )

    async def generate_frame(self, frame_plan: Dict) -> Dict[str, float]:
        """Phase 2: Convert an action description into BlendShape parameters.

        Sends a single action description (e.g. "微笑并轻轻侧头") to
        the LLM and receives back a dict of BlendShape name → float value.
        Mouth-related keys are zeroed out after generation (they will be
        overwritten by the lip-sync system).

        Args:
            frame_plan: A single frame-plan dict from ``plan_motion()``
                containing at minimum the ``description`` key.

        Returns:
            Dictionary mapping BlendShape names to float values
            in appropriate ranges (0.0–1.0 for most, degree ranges
            for Head transforms).

        Raises:
            NotImplementedError: This is a stub — implementation pending.
        """
        raise NotImplementedError(
            "MotionPlanner.generate_frame() is not yet implemented. "
            "Planned: send action description to LLM, receive BlendShape dict, "
            "filter out mouth keys, apply joint motion boost multiplier."
        )

    def _split_into_segments(
        self, text: str, total_frames: int, fps: float = 30.0
    ) -> List[Dict]:
        """Split the speech text and timeline into equal-duration segments.

        Each segment covers approximately ``self._segment_duration_ms``
        milliseconds. The last segment may be shorter.

        Args:
            text: Speech text to split (by character count per segment).
            total_frames: Total animation frames.
            fps: Frames per second (default 30).

        Returns:
            List of dicts with ``{start_frame, end_frame, text_substring}``.

        Raises:
            NotImplementedError: This is a stub.
        """
        raise NotImplementedError(
            "MotionPlanner._split_into_segments() is not yet implemented."
        )
