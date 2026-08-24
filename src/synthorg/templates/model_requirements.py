# module-kind: code
"""Structured model requirements for template agents.

Provides :class:`ModelRequirement` for expressing what kind of LLM an
agent needs: priority, context window, capability flags, family/pattern,
or an explicit example id.

A template agent references a model by one of three forms, all expressed
through :class:`ModelRequirement`:

* **explicit example id** (``model_id``): pin a concrete configured model.
* **family / pattern** (``family`` / ``model_pattern``): resolve to the
  newest configured model matching the family or glob.
* **capability** (``requires_vision`` / ``requires_reasoning`` plus
  ``priority`` / ``min_context``): let the matcher pick the best-fitting
  configured model.

Tool calling is deliberately absent from that list. Every agent turn
dispatches with tool definitions attached, so it is a floor the matcher
applies to every candidate rather than a capability a role opts into; see
:func:`~synthorg.templates.model_matcher_tiering.passes_hard_filters`.

There is no rung-string selection axis: the matcher classifies models by
real metadata, and ``ModelMatch.capability`` is report-only.
"""

from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.template import (
    TEMPLATE_MODEL_REQUIREMENT_INVALID,
    TEMPLATE_MODEL_REQUIREMENT_PARSED,
    TEMPLATE_MODEL_REQUIREMENT_RESOLVED,
)

# ``CapabilityLevel`` is re-exported for report-only consumers (``ModelMatch``);
# it is not a selection axis on ``ModelRequirement``.
__all__ = ["CapabilityLevel"]

logger = get_logger(__name__)

# Valid priority literals for the capability-scoring axis.
ModelPriority = Literal["quality", "balanced", "speed", "cost"]


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
        requires_vision: Hard-require image-input support.
        requires_reasoning: Hard-require extended-reasoning support.
        family: Resolve to the newest configured model in this family
            (e.g. ``"example-expert"``); pins a concrete id at match time.
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

    @model_validator(mode="after")
    def _validate_resolution_axes(self) -> Self:
        """Reject an explicit pin combined with a resolution hint.

        A ``model_id`` pin is selected verbatim and bypasses family/pattern
        and capability scoring, so pairing it with ``family``/``model_pattern``
        is contradictory input rather than a meaningful refinement.
        (``family`` and ``model_pattern`` together are allowed: family is the
        primary match, pattern the fallback.)

        Returns:
            The validated requirement.

        Raises:
            ValueError: When ``model_id`` coexists with ``family`` or
                ``model_pattern``.
        """
        if self.model_id is not None and (
            self.family is not None or self.model_pattern is not None
        ):
            msg = "model_id is mutually exclusive with family / model_pattern"
            raise ValueError(msg)
        return self


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


def resolve_model_requirement(
    overrides: dict[str, JsonValue] | None = None,
) -> ModelRequirement:
    """Parse a template agent's explicit model reference.

    Args:
        overrides: Explicit ``ModelRequirement`` fields from the template
            agent. A contradictory ``model_id`` + ``family`` /
            ``model_pattern`` pairing is rejected by the validator.

    Returns:
        Resolved ``ModelRequirement``.
    """
    stated: dict[str, JsonValue] = (
        {key: value for key, value in overrides.items() if value is not None}
        if overrides
        else {}
    )
    result = parse_model_requirement(stated)
    logger.debug(
        TEMPLATE_MODEL_REQUIREMENT_RESOLVED,
        model_id=result.model_id,
        priority=result.priority,
        min_context=result.min_context,
        family=result.family,
    )
    return result
