"""Configuration models for the verification subsystem."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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
