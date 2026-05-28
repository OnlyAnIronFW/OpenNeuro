"""Predefined emotion presets — local fallback BlendShape dictionaries.

These presets serve as the LOCAL fallback path when the LLM-based
ExpressionGenerator is unavailable (network outage, API error, cold start).
They are the zero-latency, always-available baseline expressions, inspired
by SoulLink's ``applyLocalExpression()`` mechanism.

Each preset maps emotion names to dictionaries of BlendShape → float value.
Values are in [0.0, 1.0] for facial parameters and [-180.0, 180.0] or
appropriate ranges for head transforms (HeadYaw, HeadPitch, HeadRoll).

There are exactly 8 presets covering the core emotional spectrum.
BlendShape values are EMPTY dicts in this stub — implementation will
fill them with realistic baseline parameters calibrated against a
standard VRM avatar (e.g. VRM-1.0 compliant Alicia Solid).
"""

from typing import Dict

# ── 8 Core Emotion Presets ──────────────────────────────────────────
# Each preset maps ARKit BlendShape name → float value (0.0–1.0).
# Head transforms (HeadYaw, HeadPitch, HeadRoll) use degree ranges.
# All dicts are EMPTY stubs — implementation will fill with calibrated values.

PRESET_EXPRESSIONS: Dict[str, Dict[str, float]] = {
    "happy": {
        # A bright, open smile with slightly squinted eyes.
        # Key BlendShapes: MouthSmileLeft/Right ~0.8,
        #   EyeSquintLeft/Right ~0.6, CheekSquintLeft/Right ~0.4,
        #   JawOpen ~0.15 (slight).
        # Head: slight upward tilt for confidence (HeadPitch ~-5).
        # Used for: positive VAD states, receiving gifts, compliments.
    },
    "sad": {
        # Drooping features, downturned mouth, inward brows.
        # Key BlendShapes: MouthFrownLeft/Right ~0.7,
        #   BrowInnerUp ~0.6, MouthRollLower ~0.4,
        #   EyeLookDownLeft/Right ~0.3.
        # Head: slight downward tilt (HeadPitch ~+8).
        # Used for: negative VAD states, game losses, insults.
    },
    "angry": {
        # Furrowed brows, pressed lips, tense posture.
        # Key BlendShapes: BrowDownLeft/Right ~0.8,
        #   MouthPressLeft/Right ~0.6, NoseSneerLeft/Right ~0.4,
        #   EyeWideLeft/Right ~0.3 (aggressive stare).
        # Head: slight forward lean (HeadPitch ~-3).
        # Used for: high arousal + negative valence, insult responses.
    },
    "surprised": {
        # Wide eyes, raised brows, open mouth.
        # Key BlendShapes: EyeWideLeft/Right ~0.9,
        #   BrowOuterUpLeft/Right ~0.8, JawOpen ~0.3,
        #   MouthFunnel ~0.2.
        # Head: slight backward lean (HeadPitch ~+3).
        # Used for: rare drops, unexpected events, sudden high-value gifts.
    },
    "shy": {
        # Asymmetric smile, averted gaze, slight blush (if supported).
        # Key BlendShapes: MouthSmileLeft ~0.5 (asymmetric),
        #   MouthSmileRight ~0.3, EyeLookOutLeft ~0.4,
        #   HeadYaw ~-8 (looking away).
        # Used for: compliments about appearance, self-referential jokes.
    },
    "thinking": {
        # Eyes looking up/away, slight head tilt, neutral mouth.
        # Key BlendShapes: EyeLookUpLeft/Right ~0.5,
        #   EyeLookOutLeft ~0.3, HeadYaw ~+10 (tilted),
        #   MouthLeft ~0.2 (thinking expression).
        # Used for: processing complex questions, between replies.
    },
    "sleepy": {
        # Heavy eyelids, relaxed features, slight head droop.
        # Key BlendShapes: EyeBlinkLeft/Right ~0.6,
        #   EyeLookDownLeft/Right ~0.4, HeadPitch ~+10,
        #   MouthShrugUpper ~0.3.
        # Used for: low arousal states, long silences, late-night streams.
    },
    "wink": {
        # One eye closed, asymmetrical smile, playful.
        # Key BlendShapes: EyeBlinkLeft ~0.8, EyeBlinkRight ~0.0,
        #   MouthSmileLeft ~0.5, CheekSquintLeft ~0.3,
        #   HeadYaw ~-5 (tilt toward wink).
        # Used for: playful remarks, private jokes, "secret" hints.
    },
}

# ── Neutral Reset State ─────────────────────────────────────────────

NEUTRAL_EXPRESSION: Dict[str, float] = {
    # All BlendShapes at 0.0 — the reset state.
    # Used by AvatarBridge's auto-reset timer after each expression.
    # Head transforms also reset to 0 (forward-facing, level).
}
