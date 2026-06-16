"""Structured model requirements and personality-based model affinity.

Provides :class:`ModelRequirement` for expressing what kind of LLM an
agent needs (tier, priority, context window, capabilities) and a
preset-keyed affinity mapping that supplies soft defaults when the
template does not specify full requirements.
"""

from types import MappingProxyType
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from synthorg.core.normalization import (
    normalize_ascii_lowercase,
    normalize_ascii_lowercase_or_default,
)
from synthorg.core.types import ModelTier, NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.template import (
    TEMPLATE_MODEL_REQUIREMENT_INVALID,
    TEMPLATE_MODEL_REQUIREMENT_PARSED,
    TEMPLATE_MODEL_REQUIREMENT_RESOLVED,
)

__all__ = ["ModelTier"]

logger = get_logger(__name__)

# Valid tier and priority literals.
ModelPriority = Literal["quality", "balanced", "speed", "cost"]

# Closed value set for personality affinity entries: a string ``priority``
# axis and an integer ``min_context`` floor.
type AffinityValue = str | int

_VALID_TIERS: frozenset[str] = frozenset(get_args(ModelTier))


class ModelRequirement(BaseModel):
    """Structured model requirement for a template agent.

    Describes *what* an agent needs from an LLM without referencing a
    specific provider or model.  Used by the matching engine to select
    the best available model.

    Attributes:
        tier: Cost/capability tier (large = most capable, small = cheapest).
        priority: Optimization axis when multiple models match a tier.
        min_context: Minimum context window in tokens (0 = no minimum).
        requires_tools: Hard-require function/tool-calling support.
        requires_vision: Hard-require image-input support.
        requires_reasoning: Hard-require extended-reasoning support.
        family: Resolve to the newest configured model in this family
            (e.g. ``"claude-sonnet"``); pins a concrete id at match time.
        model_pattern: Resolve to the newest configured model whose id
            matches this glob (e.g. ``"gpt-4*"``); pins a concrete id.
        capabilities: Future-use capability tags (e.g. ``"reasoning"``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    tier: ModelTier = Field(default="medium", description="Cost/capability tier")
    priority: ModelPriority = Field(
        default="balanced",
        description="Optimization axis for model selection",
    )
    min_context: int = Field(
        default=0,
        ge=0,
        description="Minimum context window in tokens",
    )
    requires_tools: bool = Field(
        default=False,
        description="Hard-require function/tool-calling support",
    )
    requires_vision: bool = Field(
        default=False,
        description="Hard-require image-input support",
    )
    requires_reasoning: bool = Field(
        default=False,
        description="Hard-require extended-reasoning support",
    )
    family: NotBlankStr | None = Field(
        default=None,
        description="Resolve to the newest configured model in this family",
    )
    model_pattern: NotBlankStr | None = Field(
        default=None,
        description="Resolve to the newest configured model id matching this glob",
    )
    capabilities: tuple[str, ...] = Field(
        default=(),
        description="Future-use capability tags",
    )


def parse_model_requirement(raw: str | dict[str, JsonValue]) -> ModelRequirement:
    """Parse a model requirement from a string tier or dict.

    Backward-compatible: accepts the legacy ``"medium"`` string format
    used by existing template YAML files as well as the new dict format.

    Args:
        raw: Either a tier string (``"large"``, ``"medium"``, ``"small"``)
            or a dict with ``ModelRequirement`` fields.

    Returns:
        Parsed ``ModelRequirement``.

    Raises:
        ValueError: If *raw* is a string not in the valid tier set.
        ValidationError: If *raw* is a dict with invalid fields.
    """
    if isinstance(raw, str):
        key = normalize_ascii_lowercase(raw)
        if key not in _VALID_TIERS:
            msg = f"Invalid model tier {raw!r}. Valid tiers: {sorted(_VALID_TIERS)}"
            logger.warning(
                TEMPLATE_MODEL_REQUIREMENT_INVALID,
                raw_tier=raw,
                valid_tiers=sorted(_VALID_TIERS),
            )
            raise ValueError(msg)
        result = ModelRequirement.model_validate({"tier": key})
    else:
        try:
            result = ModelRequirement.model_validate(raw)
        except ValidationError:
            logger.warning(
                TEMPLATE_MODEL_REQUIREMENT_INVALID,
                raw_requirement=raw,
                reason="dict_validation_failed",
            )
            raise

    logger.debug(
        TEMPLATE_MODEL_REQUIREMENT_PARSED,
        tier=result.tier,
        priority=result.priority,
    )
    return result


# ── Model affinity per personality preset ────────────────────
#
# Separated from the preset dicts because PersonalityConfig has
# extra="forbid".  These are soft defaults applied when resolving
# model requirements.

_RAW_AFFINITY: dict[str, dict[str, AffinityValue]] = {
    # Leaders and strategists benefit from stronger reasoning.
    "visionary_leader": {"priority": "quality", "min_context": 100_000},
    "strategic_planner": {"priority": "quality"},
    "systems_thinker": {"priority": "quality"},
    # Analysts and guardians need precision.
    "methodical_analyst": {"priority": "quality"},
    "quality_guardian": {"priority": "quality"},
    "security_sentinel": {"priority": "quality"},
    "data_driven_optimizer": {"priority": "quality"},
    "code_craftsman": {"priority": "quality"},
    "devil_advocate": {"priority": "quality"},
    # Fast movers prefer speed.
    "eager_learner": {"priority": "speed"},
    "rapid_prototyper": {"priority": "speed"},
    "growth_hacker": {"priority": "speed"},
    # Cost-conscious executors.
    "disciplined_executor": {"priority": "cost"},
    # Balanced defaults for everyone else.
    "pragmatic_builder": {"priority": "balanced"},
    "creative_innovator": {"priority": "balanced"},
    "team_diplomat": {"priority": "balanced"},
    "independent_researcher": {"priority": "balanced"},
    "empathetic_mentor": {"priority": "balanced"},
    "communication_bridge": {"priority": "balanced"},
    "user_advocate": {"priority": "balanced"},
    "process_optimizer": {"priority": "balanced"},
    "technical_communicator": {"priority": "balanced"},
    "client_advisor": {"priority": "balanced"},
}

# Both the outer mapping and each inner mapping are read-only.
MODEL_AFFINITY: MappingProxyType[str, MappingProxyType[str, AffinityValue]] = (
    MappingProxyType(
        {k: MappingProxyType(v) for k, v in _RAW_AFFINITY.items()},
    )
)
del _RAW_AFFINITY

_VALID_PRIORITIES: frozenset[str] = frozenset(get_args(ModelPriority))
assert all(  # noqa: S101
    v.get("priority", "balanced") in _VALID_PRIORITIES for v in MODEL_AFFINITY.values()
), "MODEL_AFFINITY has invalid priority values"


def resolve_model_requirement(
    tier_str: str,
    preset_name: str | None = None,
) -> ModelRequirement:
    """Merge a template tier alias with personality-preset affinity.

    The template's tier always wins.  Affinity provides ``priority``
    and ``min_context`` defaults based on the personality preset.

    Args:
        tier_str: Tier alias from the template agent config.
        preset_name: Optional personality preset name for affinity lookup.

    Returns:
        Resolved ``ModelRequirement``.
    """
    affinity: dict[str, AffinityValue] = dict(
        MODEL_AFFINITY.get(normalize_ascii_lowercase_or_default(preset_name), {}),
    )

    merged: dict[str, JsonValue] = {"tier": normalize_ascii_lowercase(tier_str)}
    # Affinity values fill in priority and min_context when available.
    if "priority" in affinity:
        merged["priority"] = affinity["priority"]
    if "min_context" in affinity:
        merged["min_context"] = affinity["min_context"]

    result = parse_model_requirement(merged)
    logger.debug(
        TEMPLATE_MODEL_REQUIREMENT_RESOLVED,
        tier=result.tier,
        priority=result.priority,
        min_context=result.min_context,
        preset=preset_name,
    )
    return result
