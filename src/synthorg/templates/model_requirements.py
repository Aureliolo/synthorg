"""Structured model requirements and personality-based model affinity.

Provides :class:`ModelRequirement` for expressing what kind of LLM an
agent needs (priority, context window, capability flags, family/pattern,
or an explicit example id) and a preset-keyed affinity mapping that
supplies capability defaults when the template does not state full
requirements.

A template agent references a model by one of three forms, all expressed
through :class:`ModelRequirement`:

* **explicit example id** (``model_id``): pin a concrete configured model.
* **family / pattern** (``family`` / ``model_pattern``): resolve to the
  newest configured model matching the family or glob.
* **capability** (``requires_tools`` / ``requires_vision`` /
  ``requires_reasoning`` plus ``priority`` / ``min_context``): let the
  matcher pick the best-fitting configured model.

There is no tier-string selection axis: the matcher classifies models by
real metadata, and ``ModelMatch.tier`` is report-only.
"""

from types import MappingProxyType
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from synthorg.core.normalization import normalize_ascii_lowercase_or_default
from synthorg.core.types import ModelTier, NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.template import (
    TEMPLATE_MODEL_REQUIREMENT_INVALID,
    TEMPLATE_MODEL_REQUIREMENT_PARSED,
    TEMPLATE_MODEL_REQUIREMENT_RESOLVED,
)

# ``ModelTier`` is re-exported for report-only consumers (``ModelMatch``);
# it is not a selection axis on ``ModelRequirement``.
__all__ = ["ModelTier"]

logger = get_logger(__name__)

# Valid priority literals for the capability-scoring axis.
ModelPriority = Literal["quality", "balanced", "speed", "cost"]

# Closed value set for personality affinity entries: a string ``priority``
# axis, an integer ``min_context`` floor, boolean ``requires_*`` flags, and
# an optional string ``family`` hint.
type AffinityValue = str | int | bool


class ModelRequirement(BaseModel):
    """Structured model requirement for a template agent.

    Describes *what* an agent needs from an LLM. Resolution order at match
    time is explicit ``model_id`` first, then ``family`` / ``model_pattern``,
    then capability scoring over the survivors of the hard filters.

    Attributes:
        model_id: Explicit configured model id (or alias) to pin. When set,
            the matcher selects this exact model and skips family/capability
            scoring.
        priority: Optimisation axis when several models clear the filters.
        min_context: Minimum context window in tokens (0 = no minimum).
        requires_tools: Hard-require function/tool-calling support.
        requires_vision: Hard-require image-input support.
        requires_reasoning: Hard-require extended-reasoning support.
        family: Resolve to the newest configured model in this family
            (e.g. ``"example-large"``); pins a concrete id at match time.
        model_pattern: Resolve to the newest configured model whose id
            matches this glob (e.g. ``"example-*"``); pins a concrete id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    model_id: NotBlankStr | None = Field(
        default=None,
        description="Explicit configured model id/alias to pin",
    )
    priority: ModelPriority = Field(
        default="balanced",
        description="Optimisation axis for capability scoring",
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


def parse_model_requirement(raw: str | dict[str, JsonValue]) -> ModelRequirement:
    """Parse a model requirement from an explicit id string or a dict.

    A bare string is an explicit example-id pin (``model_id``); a dict maps
    directly onto the :class:`ModelRequirement` capability/family fields.

    Args:
        raw: Either a non-blank model id/alias string, or a dict with
            ``ModelRequirement`` fields.

    Returns:
        Parsed ``ModelRequirement``.

    Raises:
        ValueError: If *raw* is a blank string.
        ValidationError: If *raw* is a dict with invalid fields.
    """
    if isinstance(raw, str):
        pinned = raw.strip()
        if not pinned:
            msg = "Model id reference must be a non-blank string"
            logger.warning(
                TEMPLATE_MODEL_REQUIREMENT_INVALID,
                raw_reference=raw,
                reason="blank_model_id",
            )
            raise ValueError(msg)
        result = ModelRequirement.model_validate({"model_id": pinned})
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
        model_id=result.model_id,
        priority=result.priority,
        family=result.family,
    )
    return result


# ── Model affinity per personality preset ────────────────────
#
# Separated from the preset dicts because PersonalityConfig has
# extra="forbid".  Each profile supplies capability defaults (priority,
# context floor, and hard requirement flags) that apply when the template
# agent does not state them explicitly; explicit template fields always win.

_VISIONARY_CONTEXT_FLOOR: int = 100_000

_RAW_AFFINITY: dict[str, dict[str, AffinityValue]] = {
    # Leaders and strategists reason over wide context.
    "visionary_leader": {
        "priority": "quality",
        "min_context": _VISIONARY_CONTEXT_FLOOR,
        "requires_reasoning": True,
    },
    "strategic_planner": {"priority": "quality", "requires_reasoning": True},
    "systems_thinker": {"priority": "quality", "requires_reasoning": True},
    # Analysts and guardians need precision.
    "methodical_analyst": {"priority": "quality", "requires_reasoning": True},
    "quality_guardian": {"priority": "quality"},
    "security_sentinel": {"priority": "quality", "requires_reasoning": True},
    "data_driven_optimizer": {"priority": "quality", "requires_tools": True},
    "code_craftsman": {"priority": "quality", "requires_tools": True},
    "devil_advocate": {"priority": "quality", "requires_reasoning": True},
    # Fast movers prefer speed; builders still need tool-calling.
    "eager_learner": {"priority": "speed"},
    "rapid_prototyper": {"priority": "speed", "requires_tools": True},
    "growth_hacker": {"priority": "speed"},
    # Cost-conscious executors that still run tools.
    "disciplined_executor": {"priority": "cost", "requires_tools": True},
    # Balanced defaults for everyone else.
    "pragmatic_builder": {"priority": "balanced", "requires_tools": True},
    "creative_innovator": {"priority": "balanced"},
    "team_diplomat": {"priority": "balanced"},
    "independent_researcher": {"priority": "balanced", "requires_reasoning": True},
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

# Capability-default keys a profile may contribute to a requirement.
_AFFINITY_DEFAULT_KEYS: tuple[str, ...] = (
    "priority",
    "min_context",
    "requires_tools",
    "requires_vision",
    "requires_reasoning",
    "family",
)


def resolve_model_requirement(
    preset_name: str | None = None,
    overrides: dict[str, JsonValue] | None = None,
) -> ModelRequirement:
    """Merge a personality-preset affinity profile with explicit overrides.

    The affinity profile supplies capability defaults (priority, context
    floor, hard requirement flags, optional family hint); any field the
    template states explicitly via *overrides* always wins.

    Args:
        preset_name: Optional personality preset name for affinity lookup.
        overrides: Explicit ``ModelRequirement`` fields from the template
            agent that take precedence over the affinity defaults.

    Returns:
        Resolved ``ModelRequirement``.
    """
    affinity = MODEL_AFFINITY.get(
        normalize_ascii_lowercase_or_default(preset_name),
        MappingProxyType({}),
    )
    merged: dict[str, JsonValue] = {
        key: affinity[key] for key in _AFFINITY_DEFAULT_KEYS if key in affinity
    }
    if overrides:
        merged.update({k: v for k, v in overrides.items() if v is not None})

    result = parse_model_requirement(merged)
    logger.debug(
        TEMPLATE_MODEL_REQUIREMENT_RESOLVED,
        model_id=result.model_id,
        priority=result.priority,
        min_context=result.min_context,
        family=result.family,
        preset=preset_name,
    )
    return result
