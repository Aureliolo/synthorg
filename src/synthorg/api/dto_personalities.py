"""Request/response DTOs for personality preset endpoints."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import (
    CollaborationPreference,
    CommunicationVerbosity,
    ConflictApproach,
    CreativityLevel,
    DecisionMakingStyle,
    RiskTolerance,
)
from synthorg.core.types import NotBlankStr
from synthorg.templates.preset_models import PresetSource

__all__ = [
    "PresetDetailResponse",
    "PresetSource",
    "PresetSummaryResponse",
]


# ── Responses ────────────────────────────────────────────────


class PresetSummaryResponse(BaseModel):
    """Summary of a personality preset for list endpoints."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    description: str = ""
    traits: tuple[NotBlankStr, ...] = ()
    source: PresetSource


class PresetDetailResponse(BaseModel):
    """Full personality preset definition."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    source: PresetSource
    description: str = ""
    traits: tuple[NotBlankStr, ...] = ()
    communication_style: NotBlankStr = NotBlankStr("neutral")
    risk_tolerance: RiskTolerance = RiskTolerance.MEDIUM
    creativity: CreativityLevel = CreativityLevel.MEDIUM
    openness: float = Field(default=0.5, ge=0.0, le=1.0)
    conscientiousness: float = Field(default=0.5, ge=0.0, le=1.0)
    extraversion: float = Field(default=0.5, ge=0.0, le=1.0)
    agreeableness: float = Field(default=0.5, ge=0.0, le=1.0)
    stress_response: float = Field(default=0.5, ge=0.0, le=1.0)
    decision_making: DecisionMakingStyle = DecisionMakingStyle.CONSULTATIVE
    collaboration: CollaborationPreference = CollaborationPreference.TEAM
    verbosity: CommunicationVerbosity = CommunicationVerbosity.BALANCED
    conflict_approach: ConflictApproach = ConflictApproach.COLLABORATE
    created_at: str | None = None
    updated_at: str | None = None


# ── Requests ─────────────────────────────────────────────────


class _PresetFieldsBase(BaseModel):
    """Shared personality configuration fields for request DTOs."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    traits: tuple[NotBlankStr, ...] = Field(default=(), max_length=50)
    communication_style: NotBlankStr = Field(default="neutral", max_length=100)
    risk_tolerance: RiskTolerance = RiskTolerance.MEDIUM
    creativity: CreativityLevel = CreativityLevel.MEDIUM
    description: str = Field(default="", max_length=500)
    openness: float = Field(default=0.5, ge=0.0, le=1.0)
    conscientiousness: float = Field(default=0.5, ge=0.0, le=1.0)
    extraversion: float = Field(default=0.5, ge=0.0, le=1.0)
    agreeableness: float = Field(default=0.5, ge=0.0, le=1.0)
    stress_response: float = Field(default=0.5, ge=0.0, le=1.0)
    decision_making: DecisionMakingStyle = DecisionMakingStyle.CONSULTATIVE
    collaboration: CollaborationPreference = CollaborationPreference.TEAM
    verbosity: CommunicationVerbosity = CommunicationVerbosity.BALANCED
    conflict_approach: ConflictApproach = ConflictApproach.COLLABORATE


class CreatePresetRequest(_PresetFieldsBase):
    """POST body for creating a custom personality preset."""

    name: NotBlankStr = Field(max_length=100)

    def to_config_dict(self) -> dict[str, Any]:
        """Convert to a dict suitable for PersonalityConfig validation."""
        return self.model_dump(exclude={"name"})


class UpdatePresetRequest(_PresetFieldsBase):
    """PUT body for updating a custom personality preset."""

    def to_config_dict(self) -> dict[str, Any]:
        """Convert to a dict suitable for PersonalityConfig validation."""
        return self.model_dump()
