"""Prompt rendering profiles adapted to what a model can be trusted with.

Maps each :data:`~synthorg.core.types.CapabilityLevel` to a
:class:`PromptProfile` that controls how verbose and detailed the system
prompt is.  A less capable model receives a simpler prompt it can follow
more reliably; a more capable one receives the full prompt.

Three built-in profiles:

* **full** (expert) -- no profile-driven reductions, full criteria.
* **standard** (capable) -- summary autonomy.
* **basic** (basic) -- minimal autonomy, no org policies, simplified
  acceptance criteria.

Authority and identity sections are **never** stripped regardless of
profile.
"""

from types import MappingProxyType
from typing import get_args

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import AutonomyDetailLevel, CapabilityLevel
from synthorg.observability import get_logger
from synthorg.observability.events.prompt import PROMPT_PROFILE_DEFAULT

logger = get_logger(__name__)


class PromptProfile(BaseModel):
    """Prompt rendering profile tuned for a specific capability rung.

    Controls how verbose and detailed the system prompt is, allowing
    smaller/cheaper models to receive simpler prompts that they can
    follow more reliably.

    Attributes:
        capability: The rung this profile targets.
        include_org_policies: Whether to include the org policies section.
        simplify_acceptance_criteria: Whether to render acceptance
            criteria as a flat semicolon-separated line instead of a
            nested list.
        autonomy_detail_level: Level of detail for autonomy instructions
            (``"full"`` | ``"summary"`` | ``"minimal"``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    capability: CapabilityLevel = Field(description="Target capability rung")
    include_org_policies: bool = Field(
        default=True,
        description="Whether to include org policies in prompt",
    )
    simplify_acceptance_criteria: bool = Field(
        default=False,
        description="Simplify acceptance criteria to flat list",
    )
    autonomy_detail_level: AutonomyDetailLevel = Field(
        default="full",
        description="Level of autonomy instruction detail",
    )


# ── Built-in profiles ──────────────────────────────────────────

_FULL_PROFILE = PromptProfile(
    capability="expert",
    include_org_policies=True,
    simplify_acceptance_criteria=False,
    autonomy_detail_level="full",
)

_STANDARD_PROFILE = PromptProfile(
    capability="capable",
    include_org_policies=True,
    simplify_acceptance_criteria=False,
    autonomy_detail_level="summary",
)

_BASIC_PROFILE = PromptProfile(
    capability="basic",
    include_org_policies=False,
    simplify_acceptance_criteria=True,
    autonomy_detail_level="minimal",
)

PROMPT_PROFILE_REGISTRY: MappingProxyType[CapabilityLevel, PromptProfile] = (
    MappingProxyType(
        {
            "expert": _FULL_PROFILE,
            "capable": _STANDARD_PROFILE,
            "basic": _BASIC_PROFILE,
        },
    )
)
"""Read-only mapping from capability rung to prompt profile."""

_missing_profiles = set(get_args(CapabilityLevel)) - set(PROMPT_PROFILE_REGISTRY)
if _missing_profiles:
    _msg_p = f"Missing prompt profiles for rungs: {sorted(_missing_profiles)}"
    raise ValueError(_msg_p)


def get_prompt_profile(capability: CapabilityLevel | None) -> PromptProfile:
    """Return the built-in prompt profile for a capability rung.

    When *capability* is ``None``, returns the full (expert) profile as a
    safe default -- if the rung is unknown, assume full capability.

    Args:
        capability: Capability rung, or ``None`` for the default profile.

    Returns:
        The matching ``PromptProfile``.
    """
    if capability is None:
        logger.debug(PROMPT_PROFILE_DEFAULT, default_capability="expert")
        return _FULL_PROFILE
    return PROMPT_PROFILE_REGISTRY[capability]
