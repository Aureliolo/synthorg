"""Trajectory scoring models.

Frozen Pydantic models for trajectory configuration, candidate
results, and scoring outputs.
"""

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import ExecutionResult


class TrajectoryConfig(BaseModel):
    """Configuration for best-of-K trajectory scoring.

    Attributes:
        enabled: Whether trajectory scoring is active.
        k_candidates: Number of parallel candidates to sample.
        complexity_gate: Task complexities that activate scoring.
        budget_guard_margin: Fraction of remaining budget to
            reserve (0.0--1.0).
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )

    enabled: bool = Field(
        default=False,
        description="Whether trajectory scoring is active",
    )
    k_candidates: int = Field(
        default=2,
        ge=2,
        le=5,
        description="Number of parallel candidates to sample",
    )
    complexity_gate: tuple[NotBlankStr, ...] = Field(
        default=("complex", "epic"),
        description="Task complexities that activate scoring",
    )
    budget_guard_margin: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Fraction of remaining budget to reserve",
    )


class CandidateResult(BaseModel):
    """Result from a single trajectory candidate execution.

    Attributes:
        candidate_index: Zero-based index of this candidate.
        execution_result: The execution result from this candidate.
        verbalized_confidence: LLM-reported confidence (0--100),
            or ``None`` when VC elicitation was not used.
        trace_tokens: Total output tokens across all turns.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    candidate_index: int = Field(
        ge=0,
        description="Zero-based candidate index",
    )
    execution_result: ExecutionResult = Field(
        description="Execution result from this candidate",
    )
    verbalized_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="LLM-reported confidence (0--100)",
    )
    trace_tokens: int = Field(
        ge=0,
        description="Total output tokens across all turns",
    )


class TrajectoryScore(BaseModel):
    """Scoring result for a single trajectory candidate.

    Attributes:
        candidate_index: Zero-based candidate index.
        vc_score: Log-space aggregated verbalized confidence.
        len_score: Negative trace length (shorter = better).
        joint_score: Combined VC + Len score (least-negative wins).
        consistent: Whether the candidate passed self-consistency.
    """

    # ``extra="forbid"`` is safe here despite the ``joint_score``
    # @computed_field carve-out: ``TrajectoryScore`` is constructed
    # once in ``engine/trajectory/scorer.py`` and never reconstructed
    # via ``model_dump -> model_validate``, so the computed key never
    # round-trips back into a constructor.
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    candidate_index: int = Field(
        ge=0,
        description="Zero-based candidate index",
    )
    vc_score: float = Field(
        description="Log-space verbalized confidence score",
    )
    len_score: float = Field(
        le=0.0,
        description="Negative trace length (shorter = better)",
    )
    consistent: bool = Field(
        description="Passed self-consistency filter",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Combined VC + Len (least-negative wins)",
    )
    @property
    def joint_score(self) -> float:
        """Combined score: normalized VC + Len (least-negative wins).

        VC is scaled by ``abs(len_score)`` so both signals contribute
        proportionally.  When ``len_score`` is 0 (zero-token trace),
        falls back to raw VC.
        """
        if self.len_score == 0.0:
            return self.vc_score
        # Scale VC into the same magnitude as len_score.
        # VC range is ~[-4.6, 0], len_score range is ~[-2000, 0].
        # Multiplying VC by abs(len_score) makes confidence matter.
        return self.vc_score * abs(self.len_score) + self.len_score
