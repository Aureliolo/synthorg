"""Autonomy level management — presets, resolution, and runtime changes.

Note: ``AutonomyResolver`` and ``HumanOnlyPromotionStrategy`` are **not**
re-exported here to avoid a circular import chain
(``core.company`` → ``security.autonomy.models`` → this ``__init__`` →
``resolver`` → ``security.action_types`` → ``core.enums`` → ``core``).
Import them directly from their modules when needed.
"""

from synthorg.security.autonomy.models import (
    BUILTIN_PRESETS,
    AutonomyConfig,
    AutonomyOverride,
    AutonomyPreset,
    EffectiveAutonomy,
)
from synthorg.security.autonomy.protocol import AutonomyChangeStrategy

__all__ = [
    "BUILTIN_PRESETS",
    "AutonomyChangeStrategy",
    "AutonomyConfig",
    "AutonomyOverride",
    "AutonomyPreset",
    "EffectiveAutonomy",
]
