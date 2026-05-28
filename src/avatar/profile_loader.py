"""Avatar profile loader — VRM metadata + BlendShape clustering + per-avatar prompts.

Each VRM model can carry a ``.vrm_profile.json`` sidecar file that describes:
- The mapping from 52 low-level ARKit BlendShapes to 10 high-level expressions
- Per-avatar custom system prompts for expression generation
- Physics configuration (SpringBone, Colliders — for exclusion only)

The clustering reduces the 52-dimensional ARKit parameter space into
10 human-understandable expression categories, matching the high-level
emotion primitives used by the expression generation pipeline.

BlendShape Clustering (52 → 10):
    ┌─────────────────┬──────────────────────────────────────┐
    │ Expression      │ ARKit BlendShapes                    │
    ├─────────────────┼──────────────────────────────────────┤
    │ Happy           │ MouthSmile*, CheekSquint*,           │
    │                 │ EyeSquint*, MouthDimple*             │
    │ Sad             │ MouthFrown*, BrowInnerUp,            │
    │                 │ EyeLookDown*, MouthRollLower         │
    │ Angry           │ BrowDown*, MouthPress*, NoseSneer*   │
    │ Surprised       │ EyeWide*, BrowOuterUp*, JawOpen      │
    │ Scared          │ EyeWide*, MouthFunnel, BrowInnerUp   │
    │ Disgusted       │ NoseSneer*, MouthShrugUpper,         │
    │                 │ MouthRollUpper                       │
    │ Neutral         │ (all values near 0.0)                 │
    │ Thinking        │ EyeLookUp*, EyeLookOut*, MouthLeft   │
    │ Sleepy          │ EyeBlink*, EyeLookDown*, HeadRoll    │
    │ Wink            │ EyeBlink(Left-only), MouthSmile(src) │
    └─────────────────┴──────────────────────────────────────┘

This clustering follows the SoulLink convention of treating BlendShapes
as instrumented primitives, with high-level labels for LLM prompting.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class AvatarProfile:
    """Metadata and configuration for a single VRM avatar.

    Loaded from a ``.vrm_profile.json`` sidecar file placed alongside
    the ``.vrm`` model file. If the JSON is missing, sensible defaults
    are derived from the VRM's internal BlendShape proxy.

    Attributes:
        model_path: Absolute path to the ``.vrm`` file.
        blendshape_map: Mapping from 10 high-level expression names
            (Happy, Sad, Angry, Surprised, Scared, Disgusted, Neutral,
            Thinking, Sleepy, Wink) to lists of ARKit BlendShape names
            that compose that expression.
        custom_prompt: Optional path to a ``avatar_prompt.txt`` file
            containing per-avatar instructions injected into the
            LLM system prompt (SoulLink ``model_prompt.txt`` pattern).
        physics_params: Raw physics configuration dict extracted from
            the VRM's SpringBone and Collider settings. These are NOT
            used by the expression pipeline — they are passed through
            to the renderer for informational purposes only.
        version: Schema version of the profile JSON format.
    """

    model_path: str = ""
    blendshape_map: Dict[str, List[str]] = field(default_factory=dict)
    custom_prompt: Optional[str] = None
    physics_params: Dict = field(default_factory=dict)
    version: str = "1.0.0"

    @property
    def expressions(self) -> List[str]:
        """Return the list of high-level expression names."""
        return list(self.blendshape_map.keys())


def load_avatar_profile(vrm_path: str) -> AvatarProfile:
    """Load an avatar profile from a VRM file's sidecar JSON.

    Searches for ``<vrm_filename>.vrm_profile.json`` in the same
    directory as the VRM file. If not found, derives a default
    profile from the VRM's internal BlendShape proxy definitions.

    The ``.vrm_profile.json`` format::

        {
            "version": "1.0.0",
            "model_path": "path/to/avatar.vrm",
            "blendshape_map": {
                "Happy": ["MouthSmileLeft", "MouthSmileRight", ...],
                "Sad": ["MouthFrownLeft", "MouthFrownRight", ...],
                ...
            },
            "custom_prompt": "path/to/avatar_prompt.txt",
            "physics": {
                "spring_bones": [...],
                "colliders": [...]
            }
        }

    The ``custom_prompt`` field points to a text file whose contents
    override the default LLM system prompt for expression generation.
    This enables per-avatar character expression styles
    (SoulLink ``model_prompt.txt`` pattern).

    Physics params (SpringBone, Colliders) are stored for debug/info
    but are explicitly EXCLUDED from expression generation — they
    are renderer-side concerns.

    Args:
        vrm_path: Absolute or relative path to the ``.vrm`` file.

    Returns:
        An ``AvatarProfile`` dataclass instance.

    Raises:
        NotImplementedError: This is a stub — implementation pending.
    """
    raise NotImplementedError(
        "load_avatar_profile() is not yet implemented. "
        "Planned: locate .vrm_profile.json sidecar, parse JSON, "
        "fall back to VRM internal BlendShape proxy if missing."
    )
