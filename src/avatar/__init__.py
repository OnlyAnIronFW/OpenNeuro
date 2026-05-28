"""VRM 3D avatar overlay system — LLM-driven expression control inspired by SoulLink Live2D.

This package provides the bridge between OpenNeuro's emotional state model
and a VRM 3D avatar rendered in the frontend. It transforms VAD (valence/arousal/
dominance) vectors into 52 ARKit BlendShape parameters that drive facial expressions,
with LLM-assisted expression generation for nuanced, context-aware avatar behavior.

Architecture:
    VAD EmotionalState ──► emotion_mapper ──► ExpressionGenerator (LLM)
           │                     │                       │
           │                     ▼                       ▼
           │              LOCAL fallback           AvatarBridge
           │              (vad_to_blendshapes)      │  WebSocket
           │                                        │  /ws/vrm
           ▼                                        ▼
    ProfileLoader ──► AvatarProfile ──► ExpressionCache ──► VRM Renderer
"""

from .emotion_mapper import vad_to_blendshapes
from .expression_llm import ExpressionGenerator
from .motion_planner import MotionPlanner
from .profile_loader import AvatarProfile, load_avatar_profile
from .cache import ExpressionCache
from .presets import PRESET_EXPRESSIONS
from .bridge import AvatarBridge

__all__ = [
    "AvatarBridge",
    "ExpressionGenerator",
    "MotionPlanner",
    "ProfileLoader",
    "AvatarProfile",
    "load_avatar_profile",
    "ExpressionCache",
    "PRESET_EXPRESSIONS",
    "vad_to_blendshapes",
]
