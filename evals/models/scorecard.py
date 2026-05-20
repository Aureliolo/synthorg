"""Per-release scorecard model.

The scorecard is the canonical artefact emitted by every benchmark run.
It is deterministic, schema-versioned, and round-trips through
``model_validate_json``. Downstream consumers (issue #1983 learning
curve, #1990, #1995, #1998) read the JSON form; humans read the
Markdown rendering produced by :mod:`evals.emit.markdown_writer`.
"""

from datetime import datetime  # noqa: TC003 -- Pydantic field type
from typing import Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from evals.models.brief import BriefKind  # noqa: TC001 -- Pydantic field type
from evals.scoring.aggregate import (
    GRADE_CEILING,
    GRADE_FLOOR,
    PenaltyEntry,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

# Bumping this is a deliberate, breaking change for downstream readers.
# Consumers MUST refuse to parse unknown versions; the schema is not
# additive across major bumps.
SCORECARD_SCHEMA_VERSION: Final[int] = 1

# Number of brief scores summed into the suite total. The default
# expectation is that every brief reports out of GRADE_CEILING; the
# scorecard records max_total so a viewer can compute the percentage
# without re-deriving the suite size.
MAX_PER_BRIEF: Final[int] = GRADE_CEILING

# Passing threshold: aggregate score must reach this fraction of
# max_total. Tuned to leave headroom for the reference run to clear
# while the broken run cannot.
PASS_FRACTION: Final[float] = 0.65


class JudgeCalibrationReport(BaseModel):
    """Per-rubric ordinal-calibration outcome at scoring time."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    rubric_id: NotBlankStr
    spearman_rho: float = Field(ge=-1.0, le=1.0)
    gate: float = Field(ge=-1.0, le=1.0)
    passed: bool
    anchor_count: int = Field(gt=0)


class ProcessFactReport(BaseModel):
    """Per-brief breakdown of process-fact penalties."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    events_by_class: dict[str, int] = Field(default_factory=dict)
    entries: tuple[PenaltyEntry, ...] = Field(default=())

    @property
    def is_clean(self) -> bool:
        """Whether any tracked process-fact event contributed to the penalty."""
        return not self.entries

    @field_validator("events_by_class")
    @classmethod
    def _counts_are_non_negative(cls, value: dict[str, int]) -> dict[str, int]:
        for event, count in value.items():
            if count < 0:
                msg = f"event count for {event!r} must be >= 0 (got {count})"
                raise ValueError(msg)
        return value


class BriefResult(BaseModel):
    """One row in the scorecard's per-brief table."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    brief_id: NotBlankStr
    kind: BriefKind
    grade: int = Field(ge=GRADE_FLOOR, le=GRADE_CEILING)
    deduction: int = Field(ge=0)
    score: int = Field(ge=GRADE_FLOOR, le=GRADE_CEILING)
    process_facts: ProcessFactReport
    termination_reason: NotBlankStr
    judge_calibration: JudgeCalibrationReport | None = None


class AggregatedProcessFacts(BaseModel):
    """Suite-level rollup of every brief's process-fact events."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    total_events: int = Field(default=0, ge=0)
    events_by_class: dict[str, int] = Field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        """Whether no brief in the suite emitted any tracked event."""
        return self.total_events == 0


class Scorecard(BaseModel):
    """Per-release benchmark scorecard.

    Constructed once at the end of a suite run and emitted as JSON +
    Markdown into the runner's ``out_dir``. The schema is frozen; an
    additive field requires bumping :data:`SCORECARD_SCHEMA_VERSION`
    and updating downstream consumers in the same PR.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    schema_version: int = Field(default=SCORECARD_SCHEMA_VERSION)
    generated_at: datetime
    company_config_path: NotBlankStr
    cassette_path: NotBlankStr
    cassette_sha256: NotBlankStr
    suite_version: NotBlankStr
    briefs: tuple[BriefResult, ...] = Field(min_length=1)
    process_facts: AggregatedProcessFacts
    judge_calibrations: tuple[JudgeCalibrationReport, ...] = Field(default=())

    @property
    def total(self) -> int:
        """Sum of every brief's final score."""
        return sum(b.score for b in self.briefs)

    @property
    def max_total(self) -> int:
        """Maximum achievable suite total (number of briefs * MAX_PER_BRIEF)."""
        return len(self.briefs) * MAX_PER_BRIEF

    @property
    def is_passing(self) -> bool:
        """Whether the suite cleared the pass threshold."""
        return self.total >= int(self.max_total * PASS_FRACTION)

    @field_validator("schema_version")
    @classmethod
    def _schema_version_must_be_current(cls, value: int) -> int:
        if value != SCORECARD_SCHEMA_VERSION:
            msg = (
                f"scorecard schema version mismatch: got {value}, "
                f"expected {SCORECARD_SCHEMA_VERSION}"
            )
            raise ValueError(msg)
        return value

    @field_validator("generated_at")
    @classmethod
    def _generated_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            msg = "generated_at must be timezone-aware"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _process_facts_match_briefs(self) -> Self:
        """Aggregate counts must equal the sum over the briefs."""
        from_briefs: dict[str, int] = {}
        for brief in self.briefs:
            for event_class, count in brief.process_facts.events_by_class.items():
                from_briefs[event_class] = from_briefs.get(event_class, 0) + count

        if from_briefs != self.process_facts.events_by_class:
            msg = (
                "aggregated process_facts.events_by_class disagrees with the "
                "sum across briefs; the runner emitted an inconsistent rollup"
            )
            raise ValueError(msg)

        total_from_briefs = sum(from_briefs.values())
        if total_from_briefs != self.process_facts.total_events:
            msg = (
                f"aggregated total_events ({self.process_facts.total_events}) "
                f"does not match the sum across briefs ({total_from_briefs})"
            )
            raise ValueError(msg)

        return self


__all__ = [
    "MAX_PER_BRIEF",
    "PASS_FRACTION",
    "SCORECARD_SCHEMA_VERSION",
    "AggregatedProcessFacts",
    "BriefResult",
    "JudgeCalibrationReport",
    "ProcessFactReport",
    "Scorecard",
]
