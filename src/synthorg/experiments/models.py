"""Pydantic models for the A/B experiment registry."""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field annotation


class ExperimentVariant(BaseModel):
    """A single variant registered against an experiment key.

    Attributes:
        experiment: Unique identifier for the experiment (the "what is
            under test"; e.g. ``"intake_prompt_v2"``).
        variant: Variant name within the experiment (e.g. ``"control"``,
            ``"treatment"``).
        weight: Relative weight used during deterministic assignment.
            Must be a positive integer; the assignment computes
            ``hash(subject) modulo total_weight`` and walks the variant
            list in registration order to pick the variant whose
            cumulative weight bracket contains the hash.
        description: Operator-facing description.
        created_at: When the variant was registered.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    experiment: NotBlankStr = Field(description="Experiment key (kebab-case)")
    variant: NotBlankStr = Field(description="Variant name within the experiment")
    weight: int = Field(ge=1, le=1000, description="Relative selection weight")
    description: str = Field(default="", description="Operator notes")
    created_at: AwareDatetime = Field(description="Registration timestamp (UTC)")


class ExperimentAssignment(BaseModel):
    """A recorded assignment of a subject to a variant.

    Attributes:
        experiment: Experiment key.
        subject_id: Hashed subject identifier (agent id, user id, etc.).
        variant: Variant the subject was assigned.
        assigned_at: When the assignment was first computed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    experiment: NotBlankStr = Field(description="Experiment key")
    subject_id: NotBlankStr = Field(description="Subject identifier")
    variant: NotBlankStr = Field(description="Variant the subject was assigned to")
    assigned_at: AwareDatetime = Field(description="Assignment timestamp (UTC)")
