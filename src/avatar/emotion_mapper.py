"""VAD → BlendShape mapper — local fallback when LLM is unavailable.

Converts the three-dimensional emotional state (valence/arousal/dominance)
from src/emotion/model.py into a complete set of 52 ARKit BlendShape parameters
that VRM avatars understand. This is the LOCAL path — fast, deterministic,
and always available. When the LLM-based ExpressionGenerator is online,
this mapper serves as a fallback (matching SoulLink's local presets pattern).
"""

from typing import Dict


def vad_to_blendshapes(v: float, a: float, d: float) -> Dict[str, float]:
    """Map a VAD emotional state to 52 ARKit BlendShape parameter values.

    VAD Model Reference (from ``src/emotion/model.py``):
        valence:   -1.0 (extremely negative) to +1.0 (extremely positive)
        arousal:    0.0 (very calm) to 1.0 (very excited)
        dominance:  0.0 (completely helpless) to 1.0 (fully in control)

    The 52 ARKit BlendShape names supported by VRM:
        EyeBlinkLeft, EyeLookDownLeft, EyeLookInLeft, EyeLookOutLeft,
        EyeLookUpLeft, EyeSquintLeft, EyeWideLeft, EyeBlinkRight,
        EyeLookDownRight, EyeLookInRight, EyeLookOutRight, EyeLookUpRight,
        EyeSquintRight, EyeWideRight, JawForward, JawLeft, JawRight,
        JawOpen, MouthClose, MouthFunnel, MouthPucker, MouthLeft,
        MouthRight, MouthSmileLeft, MouthSmileRight, MouthFrownLeft,
        MouthFrownRight, MouthDimpleLeft, MouthDimpleRight,
        MouthStretchLeft, MouthStretchRight, MouthRollLower,
        MouthRollUpper, MouthShrugLower, MouthShrugUpper,
        MouthPressLeft, MouthPressRight, MouthLowerDownLeft,
        MouthLowerDownRight, MouthUpperUpLeft, MouthUpperUpRight,
        BrowDownLeft, BrowDownRight, BrowInnerUp, BrowOuterUpLeft,
        BrowOuterUpRight, CheekPuff, CheekSquintLeft, CheekSquintRight,
        NoseSneerLeft, NoseSneerRight, TongueOut, HeadYaw, HeadPitch,
        HeadRoll

    Planned Mapping Strategy:
        The VAD space is divided into 6 emotional regions using
        smooth sigmoid-based interpolation between anchors:

        ┌──────────┬─────────────────────┬───────────────────┐
        │ Region   │ VAD Condition       │ Key BlendShapes   │
        ├──────────┼─────────────────────┼───────────────────┤
        │ joy      │ v > 0.3, a > 0.4   │ MouthSmile, EyeSquint, CheekSquint │
        │ sad      │ v < -0.3, a < 0.3  │ MouthFrown, BrowInnerUp, MouthRollLower │
        │ anger    │ v < -0.2, a > 0.5  │ BrowDown, MouthPress, NoseSneer │
        │ surprise │ a > 0.6, d < 0.4   │ EyeWide, BrowOuterUp, JawOpen │
        │ bored    │ a < 0.15, v < 0.1  │ EyeLookDown, MouthShrugUpper, HeadRoll │
        │ confident│ d > 0.65, v > 0.0  │ MouthSmile (asymmetric), HeadYaw │
        └──────────┴─────────────────────┴───────────────────┘

        Each region contributes its BlendShape weights scaled by the
        distance (via sigmoid) from the current VAD point to the region
        center. Multiple regions can blend simultaneously.

    Usage:
        This function is used as the LOCAL fallback when:
        1. The LLM endpoint is unreachable (network / API error)
        2. The ExpressionGenerator returns an invalid response
        3. During startup before the LLM connection is established

        Pattern matches SoulLink's ``applyLocalExpression()``:
        always available, zero-latency, deterministic.

    Args:
        v: Valence value in [-1.0, 1.0].
        a: Arousal value in [0.0, 1.0].
        d: Dominance value in [0.0, 1.0].

    Returns:
        A dictionary mapping BlendShape name to a float value in [0.0, 1.0].
        Returns an empty dict in this stub implementation.
    """
    return {}
