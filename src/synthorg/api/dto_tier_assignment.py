# module-kind: code
"""Request / response DTOs for the model tier-assignment API."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import ModelTier, NotBlankStr
from synthorg.providers.tier_assignment.models import (
    TierAssignment,
    TierProvenance,
    TierRecommendation,
)


class TierAssignmentDTO(BaseModel):
    """One model's effective tier assignment, for the dashboard."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(description="Provider name")
    model_id: NotBlankStr = Field(description="Model identifier")
    tier: ModelTier = Field(description="Effective routing tier")
    provenance: TierProvenance = Field(description="heuristic / operator / llm")
    confidence: float = Field(ge=0.0, le=1.0, description="Trust in the tier")
    reason: NotBlankStr = Field(description="Why the tier was assigned")
    is_override: bool = Field(description="Whether an override set this tier")


def to_tier_assignment_dto(assignment: TierAssignment) -> TierAssignmentDTO:
    """Map a domain :class:`TierAssignment` to its DTO.

    Returns:
        The :class:`TierAssignmentDTO` for *assignment*.
    """
    return TierAssignmentDTO(
        provider=assignment.provider,
        model_id=assignment.model_id,
        tier=assignment.tier,
        provenance=assignment.provenance,
        confidence=assignment.confidence,
        reason=assignment.reason,
        is_override=assignment.provenance != "heuristic",
    )


class TierAssignmentsResponse(BaseModel):
    """The effective tier map across all configured models."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    assignments: tuple[TierAssignmentDTO, ...] = Field(default=())


class TierOverrideRequest(BaseModel):
    """Set (or clear) an operator tier override for one model."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tier: ModelTier | None = Field(
        default=None,
        description="Override tier, or null to clear back to the heuristic",
    )
    reason: NotBlankStr = Field(
        default=NotBlankStr("operator override"),
        description="Why the override is applied",
    )


class TierRecommendationDTO(BaseModel):
    """One model's LLM tier offer (not yet applied)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(description="Provider name")
    model_id: NotBlankStr = Field(description="Model identifier")
    tier: ModelTier = Field(description="Proposed routing tier")
    confidence: float = Field(ge=0.0, le=1.0, description="Recommender confidence")
    rationale: NotBlankStr = Field(description="Recommender justification")


def to_tier_recommendation_dto(rec: TierRecommendation) -> TierRecommendationDTO:
    """Map a domain :class:`TierRecommendation` to its DTO.

    Returns:
        The :class:`TierRecommendationDTO` for *rec*.
    """
    return TierRecommendationDTO(
        provider=rec.provider,
        model_id=rec.model_id,
        tier=rec.tier,
        confidence=rec.confidence,
        rationale=rec.rationale,
    )


class TierRecommendationsResponse(BaseModel):
    """A set of LLM tier offers."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    recommendations: tuple[TierRecommendationDTO, ...] = Field(default=())


class ApplyRecommendationRequest(BaseModel):
    """Accept an LLM tier offer, writing it as an ``llm``-provenance override."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(description="Provider name")
    model_id: NotBlankStr = Field(description="Model identifier")
    tier: ModelTier = Field(description="Accepted tier")
    rationale: NotBlankStr = Field(
        default=NotBlankStr("accepted LLM recommendation"),
        description="Justification recorded on the override",
    )


class ClassifierModelDTO(BaseModel):
    """The provider + model the LLM recommender runs on."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: str = Field(default="", description="Provider name (empty = unset)")
    model_id: str = Field(default="", description="Model id (empty = unset)")


__all__ = [
    "ApplyRecommendationRequest",
    "ClassifierModelDTO",
    "TierAssignmentDTO",
    "TierAssignmentsResponse",
    "TierOverrideRequest",
    "TierRecommendationDTO",
    "TierRecommendationsResponse",
    "to_tier_assignment_dto",
    "to_tier_recommendation_dto",
]
