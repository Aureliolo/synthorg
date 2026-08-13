# module-kind: code
"""Request / response DTOs for the model tier-assignment API."""

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.providers.capability_assignment.models import (
    CapabilityAssignment,
    CapabilityProvenance,
    CapabilityRecommendation,
)


class CapabilityAssignmentDTO(BaseModel):
    """One model's effective tier assignment, for the dashboard."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(description="Provider name")
    model_id: NotBlankStr = Field(description="Model identifier")
    tier: CapabilityLevel = Field(description="Effective routing tier")
    provenance: CapabilityProvenance = Field(description="heuristic / operator / llm")
    confidence: float = Field(ge=0.0, le=1.0, description="Trust in the tier")
    reason: NotBlankStr = Field(description="Why the tier was assigned")

    @computed_field(description="Whether an override set this tier")
    @property
    def is_override(self) -> bool:
        """Whether an operator / LLM override (not the heuristic) set the tier.

        Returns:
            ``True`` unless the tier came from the deterministic heuristic.
        """
        return self.provenance != "heuristic"


def to_capability_assignment_dto(
    assignment: CapabilityAssignment,
) -> CapabilityAssignmentDTO:
    """Map a domain :class:`CapabilityAssignment` to its DTO.

    Returns:
        The :class:`CapabilityAssignmentDTO` for *assignment*.
    """
    return CapabilityAssignmentDTO(
        provider=assignment.provider,
        model_id=assignment.model_id,
        tier=assignment.tier,
        provenance=assignment.provenance,
        confidence=assignment.confidence,
        reason=assignment.reason,
    )


class CapabilityAssignmentsResponse(BaseModel):
    """The effective tier map across all configured models."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    assignments: tuple[CapabilityAssignmentDTO, ...] = Field(default=())


class CapabilityOverrideRequest(BaseModel):
    """Set (or clear) an operator tier override for one model."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tier: CapabilityLevel | None = Field(
        default=None,
        description="Override tier, or null to clear back to the heuristic",
    )
    reason: NotBlankStr = Field(
        default=NotBlankStr("operator override"),
        description="Why the override is applied",
    )


class CapabilityRecommendationDTO(BaseModel):
    """One model's LLM tier offer (not yet applied)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(description="Provider name")
    model_id: NotBlankStr = Field(description="Model identifier")
    tier: CapabilityLevel = Field(description="Proposed routing tier")
    confidence: float = Field(ge=0.0, le=1.0, description="Recommender confidence")
    rationale: NotBlankStr = Field(description="Recommender justification")


def to_capability_recommendation_dto(
    rec: CapabilityRecommendation,
) -> CapabilityRecommendationDTO:
    """Map a domain :class:`CapabilityRecommendation` to its DTO.

    Returns:
        The :class:`CapabilityRecommendationDTO` for *rec*.
    """
    return CapabilityRecommendationDTO(
        provider=rec.provider,
        model_id=rec.model_id,
        tier=rec.tier,
        confidence=rec.confidence,
        rationale=rec.rationale,
    )


class CapabilityRecommendationsResponse(BaseModel):
    """A set of LLM tier offers."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    recommendations: tuple[CapabilityRecommendationDTO, ...] = Field(default=())


class ApplyRecommendationRequest(BaseModel):
    """Accept an LLM tier offer, writing it as an ``llm``-provenance override."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(description="Provider name")
    model_id: NotBlankStr = Field(description="Model identifier")
    tier: CapabilityLevel = Field(description="Accepted tier")
    rationale: NotBlankStr = Field(
        default=NotBlankStr("accepted LLM recommendation"),
        description="Justification recorded on the override",
    )


class ClassifierModelDTO(BaseModel):
    """The provider + model the LLM recommender runs on."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: str = Field(default="", description="Provider name (empty = unset)")
    model_id: str = Field(default="", description="Model id (empty = unset)")
    enabled: bool = Field(
        default=False,
        description="Whether the LLM tier recommender opt-in is on",
    )


__all__ = [
    "ApplyRecommendationRequest",
    "CapabilityAssignmentDTO",
    "CapabilityAssignmentsResponse",
    "CapabilityOverrideRequest",
    "CapabilityRecommendationDTO",
    "CapabilityRecommendationsResponse",
    "ClassifierModelDTO",
    "to_capability_assignment_dto",
    "to_capability_recommendation_dto",
]
