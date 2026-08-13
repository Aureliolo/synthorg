# module-kind: code
"""Domain models for per-model tier assignment.

The *effective* tier of each configured model is the deterministic heuristic
classification overlaid by persisted operator / LLM-accepted overrides. Only the
overrides are persisted (as a versioned settings blob); the heuristic layer is
recomputed from live capability metadata, so it never goes stale.
"""

from typing import Final, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.types import CapabilityLevel, NotBlankStr

#: Confidence stamped on an override; an accepted override is authoritative.
_OVERRIDE_CONFIDENCE: Final[float] = 1.0

#: Where an effective tier came from. ``heuristic`` is the deterministic
#: classifier; ``operator`` is a manual override; ``llm`` is an accepted LLM
#: recommendation.
CapabilityProvenance = Literal["heuristic", "operator", "llm"]

#: Provenance values a *persisted override* may carry. The heuristic layer is
#: never persisted (it is recomputed), so an override is only operator- or
#: LLM-sourced.
OverrideProvenance = Literal["operator", "llm"]


class CapabilityAssignment(BaseModel):
    """The effective tier of one configured model, with provenance.

    Attributes:
        provider: Provider name that owns the model.
        model_id: Concrete model identifier.
        tier: The effective routing tier.
        provenance: Where the tier came from (heuristic / operator / llm).
        confidence: Trust in the tier (0-1); an override is authoritative (1.0),
            a heuristic tier carries the classifier's confidence.
        reason: Human-readable explanation for the assignment.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(description="Provider name")
    model_id: NotBlankStr = Field(description="Model identifier")
    tier: CapabilityLevel = Field(description="Effective routing tier")
    provenance: CapabilityProvenance = Field(description="Source of the tier")
    confidence: float = Field(ge=0.0, le=1.0, description="Trust in the tier")
    reason: NotBlankStr = Field(description="Explanation for the assignment")

    @model_validator(mode="after")
    def _override_is_authoritative(self) -> CapabilityAssignment:
        """Require an override to be authoritative (confidence 1.0).

        Only a heuristic tier carries a sub-1.0 classifier confidence; an
        operator- or LLM-sourced override the operator accepted is by
        definition authoritative, so a fractional-confidence override is an
        illegal state.

        Returns:
            The validated model.

        Raises:
            ValueError: When a non-heuristic tier carries confidence != 1.0.
        """
        if self.provenance != "heuristic" and self.confidence != _OVERRIDE_CONFIDENCE:
            msg = (
                f"{self.provenance} override must be authoritative "
                f"(confidence {_OVERRIDE_CONFIDENCE}), got {self.confidence}"
            )
            raise ValueError(msg)
        return self


class CapabilityOverride(BaseModel):
    """A persisted operator / LLM override of one model's tier.

    Attributes:
        provider: Provider name that owns the model.
        model_id: Concrete model identifier.
        tier: The overridden routing tier.
        provenance: Whether an operator set it or it is an accepted LLM offer.
        reason: Why the override was applied.
        updated_at: When the override was last written.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(description="Provider name")
    model_id: NotBlankStr = Field(description="Model identifier")
    tier: CapabilityLevel = Field(description="Overridden routing tier")
    provenance: OverrideProvenance = Field(description="Operator or accepted LLM")
    reason: NotBlankStr = Field(description="Why the override was applied")
    updated_at: AwareDatetime = Field(description="When the override was written")


CAPABILITY_ASSIGNMENT_SCHEMA_VERSION: Final[int] = 1


class CapabilityOverrideMap(BaseModel):
    """Versioned envelope for the persisted tier-override blob.

    Wrapping the overrides in a versioned envelope lets the reader reject a
    blob written by an incompatible schema and fall back to an empty map rather
    than mis-parsing it, mirroring
    :class:`~synthorg.config.provider_schema.ProvidersConfigEnvelope`.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    schema_version: int = Field(
        default=CAPABILITY_ASSIGNMENT_SCHEMA_VERSION,
        description="Schema version of the persisted override blob",
    )
    overrides: tuple[CapabilityOverride, ...] = Field(
        default=(),
        description="Persisted operator / LLM tier overrides",
    )

    @model_validator(mode="after")
    def _unique_per_model(self) -> CapabilityOverrideMap:
        """Reject two overrides for the same ``(provider, model_id)``.

        The service composes the effective map by indexing overrides on
        ``(provider, model_id)``, so a duplicate would silently resolve to
        whichever entry is last in tuple order. A duplicate in the persisted
        blob (hand-edited or written by a buggy caller) is a corruption the
        reader must reject rather than mask.

        Returns:
            The validated model.

        Raises:
            ValueError: When two overrides target the same model.
        """
        keys = [(o.provider, o.model_id) for o in self.overrides]
        if len(set(keys)) != len(keys):
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            msg = f"duplicate tier overrides for {dupes}"
            raise ValueError(msg)
        return self


class CapabilityRecommendation(BaseModel):
    """An LLM tier offer for one model (not yet applied).

    Attributes:
        provider: Provider name that owns the model.
        model_id: Concrete model identifier.
        tier: The tier the recommender proposes.
        confidence: The recommender's confidence (0-1).
        rationale: The recommender's justification.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(description="Provider name")
    model_id: NotBlankStr = Field(description="Model identifier")
    tier: CapabilityLevel = Field(description="Proposed routing tier")
    confidence: float = Field(ge=0.0, le=1.0, description="Recommender confidence")
    rationale: NotBlankStr = Field(description="Recommender justification")


__all__ = [
    "CAPABILITY_ASSIGNMENT_SCHEMA_VERSION",
    "CapabilityAssignment",
    "CapabilityOverride",
    "CapabilityOverrideMap",
    "CapabilityProvenance",
    "CapabilityRecommendation",
    "OverrideProvenance",
]
