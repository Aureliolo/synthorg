# module-kind: code
"""The scoreboard artifact: what was measured, how it scored, what to promote.

This is a sibling of :class:`evals.models.scorecard.Scorecard`, not an extension
of it. The scorecard grades a company suite and records neither cost, turns,
wall-clock nor the commit under test; bolting those on would mean a breaking
schema bump for every existing consumer. The A/B needs all four, so it carries
its own schema-versioned root.

Two reporting rules the models enforce structurally:

* A loop that could not be measured is reported as unavailable with its reason.
  It is never silently omitted, and never fabricated as a zero.
* Spend is broken down per ``(provider, model)`` rather than collapsed to one
  figure, because an organisation runs several providers and a single blended
  number would hide which one the cost actually came from.
"""

from datetime import datetime
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evals.loop_ab.aggregate import LoopRepetitionSummary
from evals.loop_ab.promotion import PromotionRecommendation
from evals.loop_ab.rubric import (
    CORRECTNESS_GATE_FLOOR,
    RUBRIC_WEIGHT_CORRECTNESS,
    RUBRIC_WEIGHT_LATENCY,
    RUBRIC_WEIGHT_RESILIENCE,
    RUBRIC_WEIGHT_TOKENS,
    RUBRIC_WEIGHT_TURNS,
    LoopCellScore,
)
from synthorg.core.types import NotBlankStr

#: Bumping this is a deliberate, breaking change for downstream readers.
LOOP_AB_SCHEMA_VERSION: Final[int] = 1


class ProviderSpend(BaseModel):
    """Authoritative spend for one ``(provider, model)`` within a run.

    Sourced from the gateway's cost ledger, which is the single chokepoint every
    loop's dispatch passes through, so this is a measurement rather than an
    estimate re-derived from token counts and a price list.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    provider: NotBlankStr
    model_id: NotBlankStr
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: float = Field(ge=0.0)
    currency: NotBlankStr


class RubricWeights(BaseModel):
    """The weights this scoreboard was scored under.

    Stamped into every artifact so a scoreboard is self-describing: a reader
    never has to guess which revision of the rubric produced a ranking, and a
    re-weighting is visible in the diff.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    correctness: int = Field(ge=0)
    tokens: int = Field(ge=0)
    latency: int = Field(ge=0)
    turns: int = Field(ge=0)
    resilience: int = Field(ge=0)
    correctness_gate_floor: float = Field(ge=0.0)

    @classmethod
    def current(cls) -> Self:
        """Capture the rubric weights currently in force.

        Returns:
            The active :class:`RubricWeights`.
        """
        return cls(
            correctness=RUBRIC_WEIGHT_CORRECTNESS,
            tokens=RUBRIC_WEIGHT_TOKENS,
            latency=RUBRIC_WEIGHT_LATENCY,
            turns=RUBRIC_WEIGHT_TURNS,
            resilience=RUBRIC_WEIGHT_RESILIENCE,
            correctness_gate_floor=CORRECTNESS_GATE_FLOOR,
        )


class Provenance(BaseModel):
    """What this scoreboard was measured against.

    The commit SHA is the load-bearing field. Loop-completion semantics are
    still moving, so a scoreboard measured against an older commit may be
    describing loops that no longer behave that way; stamping the commit makes
    a stale scoreboard self-evident instead of quietly authoritative.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    generated_at: datetime
    git_commit: NotBlankStr
    git_dirty: bool
    manifest_sha256: NotBlankStr
    brief_suite_version: NotBlankStr

    @field_validator("generated_at")
    @classmethod
    def _generated_at_must_be_aware(cls, value: datetime) -> datetime:
        """Reject naive timestamps so artifacts order unambiguously."""
        if value.tzinfo is None:
            msg = "generated_at must be timezone-aware"
            raise ValueError(msg)
        return value


class LoopBriefRow(BaseModel):
    """One ``(loop, brief, tier)`` result.

    Invariant: a row is either measured (carrying a score and its measurements)
    or unavailable (carrying a reason), never both and never neither. That is
    what stops an unwired loop from being quietly dropped from the comparison.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    loop_type: NotBlankStr
    brief_id: NotBlankStr
    tier: NotBlankStr
    model_id: NotBlankStr
    score: LoopCellScore | None = None
    measurement: LoopRepetitionSummary | None = None
    spend: tuple[ProviderSpend, ...] = ()
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def _measured_xor_unavailable(self) -> Self:
        """Enforce that a row is either fully measured or explicitly unavailable."""
        measured = self.score is not None and self.measurement is not None
        unavailable = self.unavailable_reason is not None
        if measured == unavailable:
            msg = (
                f"LoopBriefRow {self.loop_type!r}/{self.brief_id!r}: a row must be "
                "either measured (score + measurement) or unavailable (reason), "
                f"got score={self.score is not None}, "
                f"measurement={self.measurement is not None}, "
                f"unavailable_reason={self.unavailable_reason!r}"
            )
            raise ValueError(msg)
        return self


class Scoreboard(BaseModel):
    """The committed A/B report artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    schema_version: int = Field(default=LOOP_AB_SCHEMA_VERSION)
    provenance: Provenance
    weights: RubricWeights
    rows: tuple[LoopBriefRow, ...] = Field(min_length=1)
    recommendation: PromotionRecommendation

    @field_validator("schema_version")
    @classmethod
    def _schema_version_must_be_current(cls, value: int) -> int:
        """Reject a scoreboard built against a mismatched schema version."""
        if value != LOOP_AB_SCHEMA_VERSION:
            msg = (
                f"scoreboard schema version mismatch: got {value}, "
                f"expected {LOOP_AB_SCHEMA_VERSION}"
            )
            raise ValueError(msg)
        return value

    @property
    def measured_rows(self) -> tuple[LoopBriefRow, ...]:
        """Rows that carry a real measurement."""
        return tuple(row for row in self.rows if row.score is not None)

    @property
    def unavailable_rows(self) -> tuple[LoopBriefRow, ...]:
        """Rows for loops that could not be measured, with their reasons."""
        return tuple(row for row in self.rows if row.unavailable_reason is not None)

    @property
    def total_cost(self) -> float:
        """Total measured spend across every row and provider."""
        return sum(spend.cost for row in self.rows for spend in row.spend)


__all__ = [
    "LOOP_AB_SCHEMA_VERSION",
    "LoopBriefRow",
    "Provenance",
    "ProviderSpend",
    "RubricWeights",
    "Scoreboard",
]
