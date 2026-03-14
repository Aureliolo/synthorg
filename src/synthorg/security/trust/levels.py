"""Shared trust level ordering and transition constants.

Provides canonical level ordering, rank lookup, and transition keys
used consistently across all trust strategies and the trust service.
"""

from synthorg.core.enums import ToolAccessLevel

# Canonical trust level ordering (lowest to highest).
TRUST_LEVEL_ORDER: tuple[ToolAccessLevel, ...] = (
    ToolAccessLevel.SANDBOXED,
    ToolAccessLevel.RESTRICTED,
    ToolAccessLevel.STANDARD,
    ToolAccessLevel.ELEVATED,
)

# Rank lookup for level comparison.
TRUST_LEVEL_RANK: dict[ToolAccessLevel, int] = {
    level: idx for idx, level in enumerate(TRUST_LEVEL_ORDER)
}

# Transition key convention: "{from_level}_to_{to_level}"
TRANSITION_KEYS: tuple[tuple[str, ToolAccessLevel, ToolAccessLevel], ...] = (
    (
        "sandboxed_to_restricted",
        ToolAccessLevel.SANDBOXED,
        ToolAccessLevel.RESTRICTED,
    ),
    (
        "restricted_to_standard",
        ToolAccessLevel.RESTRICTED,
        ToolAccessLevel.STANDARD,
    ),
    (
        "standard_to_elevated",
        ToolAccessLevel.STANDARD,
        ToolAccessLevel.ELEVATED,
    ),
)
