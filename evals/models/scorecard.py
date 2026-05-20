"""Per-release scorecard model.

The scorecard is the canonical artefact emitted by every benchmark
run. It is deterministic, schema-versioned, and round-trips through
``model_validate_json``: the JSON form is the machine-readable wire
contract; humans read the Markdown rendering produced by
:mod:`evals.emit.markdown_writer`.
"""

import math
from datetime import datetime  # noqa: TC003 -- Pydantic field type
from typing import Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from evals.models.brief import BriefKind
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
    """Per-rubric ordinal-calibration outcome at scoring time.

    Invariant: ``passed`` MUST equal ``spearman_rho >= gate``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    rubric_id: NotBlankStr
    spearman_rho: float = Field(ge=-1.0, le=1.0)
    gate: float = Field(ge=-1.0, le=1.0)
    passed: bool
    anchor_count: int = Field(gt=0)

    @model_validator(mode="after")
    def _passed_matches_gate(self) -> Self:
        """Enforce ``passed == (spearman_rho >= gate)`` at construction time."""
        expected = self.spearman_rho >= self.gate
        if self.passed != expected:
            msg = (
                f"JudgeCalibrationReport {self.rubric_id!r}: "
                f"passed={self.passed} does not match "
                f"spearman_rho={self.spearman_rho:.3f} >= gate={self.gate}"
            )
            raise ValueError(msg)
        return self


class ProcessFactReport(BaseModel):
    """Per-brief breakdown of process-fact penalties."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    events_by_class: dict[str, int] = Field(default_factory=dict)
    entries: tuple[PenaltyEntry, ...] = Field(default=())

    # ``@computed_field`` is rejected here: this model is round-tripped via
    # ``model_validate_json``, and a serialised ``is_clean`` would land in
    # the input dict and trip ``extra="forbid"`` on reparse. The project's
    # existing derived-field pattern (e.g. ``Scorecard.total``) is the same.
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

    @model_validator(mode="after")
    def _entries_match_events_by_class(self) -> Self:
        """Reject a report whose entries disagree with events_by_class.

        Each penalty entry counts the same events as the corresponding
        event class; the two views must stay in sync so the scorecard
        can be audited from either field.
        """
        from_entries: dict[str, int] = {}
        for entry in self.entries:
            from_entries[entry.event_constant] = (
                from_entries.get(entry.event_constant, 0) + entry.count
            )
        tracked_in_events = {
            event: count
            for event, count in self.events_by_class.items()
            if event in from_entries
        }
        if from_entries != tracked_in_events:
            msg = (
                "ProcessFactReport: penalty entries disagree with the "
                "tracked events_by_class slice "
                f"(entries={from_entries}, tracked={tracked_in_events})"
            )
            raise ValueError(msg)
        return self


class BriefResult(BaseModel):
    """One row in the scorecard's per-brief table.

    Invariant: ``score == max(grade - deduction, score_floor)``. The
    validator below enforces it at construction time so a manually
    built ``BriefResult`` cannot ship an internally inconsistent row.
    The floor flows from :class:`evals.scoring.aggregate.AggregationResult`
    (which in turn comes from :attr:`PenaltyTable.floor`) so the
    scorecard records the exact lower bound the aggregator applied.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    brief_id: NotBlankStr
    kind: BriefKind
    grade: int = Field(ge=GRADE_FLOOR, le=GRADE_CEILING)
    deduction: int = Field(ge=0)
    score: int = Field(ge=GRADE_FLOOR, le=GRADE_CEILING)
    score_floor: int = Field(default=GRADE_FLOOR, ge=GRADE_FLOOR, le=GRADE_CEILING)
    process_facts: ProcessFactReport
    termination_reason: NotBlankStr
    judge_calibration: JudgeCalibrationReport | None = None

    @model_validator(mode="after")
    def _kind_matches_judge_calibration(self) -> Self:
        """Enforce ``judged`` <=> ``judge_calibration is not None``."""
        if self.kind is BriefKind.JUDGED and self.judge_calibration is None:
            msg = (
                f"BriefResult {self.brief_id!r}: kind={self.kind.value} "
                "requires a judge_calibration report"
            )
            raise ValueError(msg)
        if self.kind is not BriefKind.JUDGED and self.judge_calibration is not None:
            msg = (
                f"BriefResult {self.brief_id!r}: kind={self.kind.value} "
                "must not carry a judge_calibration report"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _score_matches_grade_minus_deduction(self) -> Self:
        """Enforce the floored score invariant at construction time."""
        expected = max(self.grade - self.deduction, self.score_floor)
        if self.score != expected:
            msg = (
                f"BriefResult {self.brief_id!r}: score={self.score} "
                f"does not match expected {expected} "
                f"(grade={self.grade} - deduction={self.deduction}, "
                f"floored at {self.score_floor})"
            )
            raise ValueError(msg)
        return self


class AggregatedProcessFacts(BaseModel):
    """Suite-level rollup of every brief's process-fact events.

    Invariant: ``total_events == sum(events_by_class.values())``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    total_events: int = Field(default=0, ge=0)
    events_by_class: dict[str, int] = Field(default_factory=dict)

    # ``@property`` (not ``@computed_field``) for the same round-trip
    # reason as ``ProcessFactReport.is_clean`` above.
    @property
    def is_clean(self) -> bool:
        """Whether no brief in the suite emitted any tracked event."""
        return self.total_events == 0

    @field_validator("events_by_class")
    @classmethod
    def _counts_are_non_negative(cls, value: dict[str, int]) -> dict[str, int]:
        """Reject negative per-class counts; aggregation invariants assume ge=0."""
        for event, count in value.items():
            if count < 0:
                msg = f"event count for {event!r} must be >= 0 (got {count})"
                raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _total_matches_class_sum(self) -> Self:
        """Reject a rollup whose total_events disagrees with the per-class sum."""
        class_sum = sum(self.events_by_class.values())
        if self.total_events != class_sum:
            msg = (
                f"AggregatedProcessFacts: total_events={self.total_events} "
                f"does not match sum of events_by_class={class_sum}"
            )
            raise ValueError(msg)
        return self


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
        """Whether the suite cleared the pass threshold.

        Uses ``math.ceil`` (not ``int``) so a fractional threshold
        rounds UP to the next integer; otherwise the required score
        could silently relax (e.g. ``int(295 * 0.65)`` is 191 while the
        intended bar at 65% of 295 is 192).
        """
        return self.total >= math.ceil(self.max_total * PASS_FRACTION)

    @field_validator("schema_version")
    @classmethod
    def _schema_version_must_be_current(cls, value: int) -> int:
        """Reject scorecards built against a mismatched schema version."""
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
        """Reject naive timestamps; UTC-aware values are required for emit order."""
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
