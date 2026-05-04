"""Configuration models for the verification subsystem."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import ModelTier  # noqa: TC001


class DecomposerVariant(StrEnum):
    """Discriminator for criteria decomposition strategies."""

    LLM = "llm"
    IDENTITY = "identity"


class GraderVariant(StrEnum):
    """Discriminator for rubric grading strategies."""

    LLM = "llm"
    HEURISTIC = "heuristic"


class VerificationConfig(BaseModel):
    """Configuration for the verification subsystem.

    Attributes:
        decomposer: Decomposition strategy variant.
        grader: Grading strategy variant.
        decomposer_model_tier: Model tier for LLM decomposer.
        grader_model_tier: Model tier for LLM grader.
        max_probes_per_criterion: Maximum probes per criterion.
        min_confidence_override: Override rubric min_confidence.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    decomposer: DecomposerVariant = Field(
        default=DecomposerVariant.IDENTITY,
        description="Decomposition strategy",
    )
    grader: GraderVariant = Field(
        default=GraderVariant.HEURISTIC,
        description="Grading strategy",
    )
    decomposer_model_tier: ModelTier = Field(
        default="medium",
        description="Model tier for LLM decomposer",
    )
    grader_model_tier: ModelTier = Field(
        default="medium",
        description="Model tier for LLM grader",
    )
    max_probes_per_criterion: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum probes per criterion",
    )
    min_confidence_override: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Override rubric min_confidence",
    )
