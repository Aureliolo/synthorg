"""Request/response DTOs for personality preset endpoints."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import (
    CollaborationPreference,
    CommunicationVerbosity,
    ConflictApproach,
    CreativityLevel,
    DecisionMakingStyle,
    RiskTolerance,
)
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

    name: NotBlankStr = Field(description="Unique preset name.")
    description: str = Field(default="", description="Short human-readable summary.")
    traits: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Headline personality traits for the preset.",
    )
    source: PresetSource = Field(
        description="Whether the preset is built-in or custom.",
    )


class PresetDetailResponse(BaseModel):
    """Full personality preset definition."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Unique preset name.")
    source: PresetSource = Field(
        description="Whether the preset is built-in or custom.",
    )
    description: str = Field(default="", description="Full preset description.")
    traits: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Personality traits for the preset.",
    )
    communication_style: NotBlankStr = Field(
        default=NotBlankStr("neutral"),
        description="Default communication style label.",
    )
    risk_tolerance: RiskTolerance = Field(
        default=RiskTolerance.MEDIUM,
        description="Risk-tolerance disposition.",
    )
    creativity: CreativityLevel = Field(
        default=CreativityLevel.MEDIUM,
        description="Creativity disposition.",
    )
    openness: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Big Five openness score in the range 0 to 1.",
    )
    conscientiousness: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Big Five conscientiousness score in the range 0 to 1.",
    )
    extraversion: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Big Five extraversion score in the range 0 to 1.",
    )
    agreeableness: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Big Five agreeableness score in the range 0 to 1.",
    )
    stress_response: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Stress-response (neuroticism) score in the range 0 to 1.",
    )
    decision_making: DecisionMakingStyle = Field(
        default=DecisionMakingStyle.CONSULTATIVE,
        description="Default decision-making style.",
    )
    collaboration: CollaborationPreference = Field(
        default=CollaborationPreference.TEAM,
        description="Default collaboration preference.",
    )
    verbosity: CommunicationVerbosity = Field(
        default=CommunicationVerbosity.BALANCED,
        description="Default communication verbosity.",
    )
    conflict_approach: ConflictApproach = Field(
        default=ConflictApproach.COLLABORATE,
        description="Default conflict-handling approach.",
    )
    created_at: str | None = Field(
        default=None,
        description="Creation timestamp as an ISO 8601 string, if known.",
    )
    updated_at: str | None = Field(
        default=None,
        description="Last-update timestamp as an ISO 8601 string, if known.",
    )


# ── Requests ─────────────────────────────────────────────────


class _PresetFieldsBase(BaseModel):
    """Shared personality configuration fields for request DTOs."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    traits: tuple[NotBlankStr, ...] = Field(
        default=(),
        max_length=50,
        description="Personality traits for the preset.",
    )
    communication_style: NotBlankStr = Field(
        default="neutral",
        max_length=100,
        description="Communication style label.",
    )
    risk_tolerance: RiskTolerance = Field(
        default=RiskTolerance.MEDIUM,
        description="Risk-tolerance disposition.",
    )
    creativity: CreativityLevel = Field(
        default=CreativityLevel.MEDIUM,
        description="Creativity disposition.",
    )
    description: str = Field(
        default="",
        max_length=500,
        description="Preset description.",
    )
    openness: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Big Five openness score in the range 0 to 1.",
    )
    conscientiousness: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Big Five conscientiousness score in the range 0 to 1.",
    )
    extraversion: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Big Five extraversion score in the range 0 to 1.",
    )
    agreeableness: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Big Five agreeableness score in the range 0 to 1.",
    )
    stress_response: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Stress-response (neuroticism) score in the range 0 to 1.",
    )
    decision_making: DecisionMakingStyle = Field(
        default=DecisionMakingStyle.CONSULTATIVE,
        description="Decision-making style.",
    )
    collaboration: CollaborationPreference = Field(
        default=CollaborationPreference.TEAM,
        description="Collaboration preference.",
    )
    verbosity: CommunicationVerbosity = Field(
        default=CommunicationVerbosity.BALANCED,
        description="Communication verbosity.",
    )
    conflict_approach: ConflictApproach = Field(
        default=ConflictApproach.COLLABORATE,
        description="Conflict-handling approach.",
    )


class CreatePresetRequest(_PresetFieldsBase):
    """POST body for creating a custom personality preset."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr = Field(max_length=100)

    def to_config_dict(self) -> dict[str, JsonValue]:
        """Convert to a dict suitable for PersonalityConfig validation.

        Returns:
            Mapping matching the ``dict[str, JsonValue]`` annotation.
        """
        return self.model_dump(exclude={"name"})


class UpdatePresetRequest(_PresetFieldsBase):
    """PUT body for updating a custom personality preset."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    def to_config_dict(self) -> dict[str, JsonValue]:
        """Convert to a dict suitable for PersonalityConfig validation.

        Returns:
            Mapping matching the ``dict[str, JsonValue]`` annotation.
        """
        return self.model_dump()
