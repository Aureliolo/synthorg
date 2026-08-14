# module-kind: code
"""Turn published scores into capability rungs.

A rung is decided by a model's **rank within the source that measured it**,
never by its raw score. Sources publish on their own scales (a pass rate,
a normalised rating, a resolve percentage), so a shared numeric threshold
would grade the whole of one source below the whole of another while
measuring nothing about either. A percentile is scale-free, which means
one pair of settings serves every source including ones added later.

The cohort a model ranks against is that source's own recently-measured
population. Ranking against everything a source ever published would let a
model look strong purely because the list behind it is full of models
nobody would configure today.

Where two sources disagree the LOWER rung wins. Over-grading routes work to
a model that cannot do it; under-grading routes it to something better than
it needed, and only one of those is a failure.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import CapabilityLevel, NotBlankStr, capability_rank
from synthorg.providers.capability_sources.models import (
    CapabilityAxis,
    CapabilityScore,
)

#: Below this many measured models a percentile says nothing: the top of a
#: two-model list is the better of two, not an expert. A source with a
#: cohort this small grades nothing and the heuristic keeps the model.
_MIN_COHORT: Final[int] = 5


class CapabilityThresholds(BaseModel):
    """Where the rung boundaries sit on the within-source percentile.

    Attributes:
        expert_percentile: At or above this rank fraction a model is
            ``expert``.
        capable_percentile: At or above this rank fraction (and below the
            expert line) a model is ``capable``; below it, ``basic``.
        max_age_days: How old a measurement may be and still count. A row
            older than this neither grades its model nor pads the cohort.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    expert_percentile: float = Field(
        ge=0.0,
        le=1.0,
        description="Rank fraction at or above which a model is expert",
    )
    capable_percentile: float = Field(
        ge=0.0,
        le=1.0,
        description="Rank fraction at or above which a model is capable",
    )
    max_age_days: int = Field(
        gt=0,
        description="Maximum age of a measurement that still counts",
    )

    @model_validator(mode="after")
    def _ordered(self) -> CapabilityThresholds:
        """Require the capable line to sit below the expert line.

        Returns:
            The validated thresholds.

        Raises:
            ValueError: When the two boundaries cross, which would make one
                of the three rungs unreachable.
        """
        if self.capable_percentile >= self.expert_percentile:
            msg = (
                f"capable_percentile ({self.capable_percentile}) must sit "
                f"below expert_percentile ({self.expert_percentile}); "
                f"otherwise no model can be graded capable"
            )
            raise ValueError(msg)
        return self


class EvidenceGrade(BaseModel):
    """One source's verdict on one model, with everything behind it.

    Attributes:
        capability: The rung this source's measurements put the model on.
        source_label: Which source measured it.
        model_identifier: The identifier the source used, verbatim.
        percentile: Where the model sits in its source's cohort (0-1).
        cohort_size: How many models it was ranked against. A grade from a
            small cohort is weaker evidence and says so.
        axes_used: Which axes contributed, so a coding-only measurement is
            not mistaken for an all-round one.
        as_of: When the source measured it, newest axis first.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    capability: CapabilityLevel = Field(description="Rung the evidence supports")
    source_label: NotBlankStr = Field(description="Source that measured it")
    model_identifier: NotBlankStr = Field(description="Identifier the source used")
    percentile: float = Field(ge=0.0, le=1.0, description="Rank within the cohort")
    cohort_size: int = Field(ge=0, description="Models ranked against")
    axes_used: tuple[CapabilityAxis, ...] = Field(description="Contributing axes")
    as_of: AwareDatetime = Field(description="When the source measured it")


class _Measured(BaseModel):
    """One model's collapsed measurement within one source."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    model_identifier: NotBlankStr
    mean_score: float
    axes_used: tuple[CapabilityAxis, ...]
    as_of: AwareDatetime


def _collapse(
    rows: Iterable[CapabilityScore],
) -> dict[str, _Measured]:
    """Collapse one source's per-axis rows into one measurement per model.

    A model's axes are averaged rather than reduced to its best, so a
    specialist strong on one axis and weak on another lands mid-ladder
    instead of inheriting its speciality's standing everywhere.

    Returns:
        The per-model measurement, keyed by the source's identifier.
    """
    by_model: dict[str, list[CapabilityScore]] = {}
    for row in rows:
        by_model.setdefault(str(row.model_identifier), []).append(row)
    collapsed: dict[str, _Measured] = {}
    for identifier, scores in by_model.items():
        collapsed[identifier] = _Measured(
            model_identifier=NotBlankStr(identifier),
            mean_score=sum(s.score for s in scores) / len(scores),
            axes_used=tuple(sorted({s.axis for s in scores})),
            as_of=max(s.as_of for s in scores),
        )
    return collapsed


def _capability_for(
    percentile: float,
    thresholds: CapabilityThresholds,
) -> CapabilityLevel:
    """Map a percentile onto its rung.

    Returns:
        The rung the percentile falls in.
    """
    if percentile >= thresholds.expert_percentile:
        return "expert"
    if percentile >= thresholds.capable_percentile:
        return "capable"
    return "basic"


def _grade_one_source(
    source_label: str,
    measured: Mapping[str, _Measured],
    thresholds: CapabilityThresholds,
) -> dict[tuple[str, str], EvidenceGrade]:
    """Rank one source's cohort and grade every model in it.

    Returns:
        The grades for this source, keyed by ``(source, identifier)``.
    """
    cohort_size = len(measured)
    if cohort_size < _MIN_COHORT:
        return {}
    ordered = sorted(
        measured.values(),
        key=lambda m: (m.mean_score, m.model_identifier),
    )
    # Rank is the fraction of the cohort a model stands at or above, so the
    # weakest sits at 0.0 and the strongest just below 1.0 rather than at it:
    # nothing outranks the whole cohort including itself.
    graded: dict[tuple[str, str], EvidenceGrade] = {}
    for index, entry in enumerate(ordered):
        percentile = index / cohort_size
        graded[(source_label, str(entry.model_identifier))] = EvidenceGrade(
            capability=_capability_for(percentile, thresholds),
            source_label=NotBlankStr(source_label),
            model_identifier=entry.model_identifier,
            percentile=percentile,
            cohort_size=cohort_size,
            axes_used=entry.axes_used,
            as_of=entry.as_of,
        )
    return graded


def grade_sources(
    scores: Sequence[CapabilityScore],
    *,
    thresholds: CapabilityThresholds,
    now: datetime,
) -> dict[tuple[str, str], EvidenceGrade]:
    """Grade every model each source measured recently enough to count.

    Args:
        scores: Persisted per-axis measurements across every source.
        thresholds: Where the rung boundaries sit, and how old a
            measurement may be.
        now: Current time, for the recency cut.

    Returns:
        One grade per ``(source_label, model_identifier)`` a source graded.
        A source whose recent cohort is too small to rank within grades
        nothing, and contributes no entries.
    """
    cutoff = now - timedelta(days=thresholds.max_age_days)
    by_source: dict[str, list[CapabilityScore]] = {}
    for row in scores:
        if row.as_of < cutoff:
            continue
        by_source.setdefault(str(row.source_label), []).append(row)

    graded: dict[tuple[str, str], EvidenceGrade] = {}
    for source_label, rows in by_source.items():
        graded.update(
            _grade_one_source(source_label, _collapse(rows), thresholds),
        )
    return graded


def resolve_evidence_grade(
    grades: Mapping[tuple[str, str], EvidenceGrade],
    *,
    model_identifier: str,
) -> EvidenceGrade | None:
    """Settle every source's verdict on one model into a single grade.

    Returns:
        The grade from whichever source rated the model lowest, or ``None``
        when no source measured it. Returning one source's own grade rather
        than a synthesised average keeps the provenance answerable: the
        rung an operator sees is one a named source actually produced.
    """
    candidates = [g for (_, ident), g in grades.items() if ident == model_identifier]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda g: (capability_rank(g.capability), g.percentile),
    )


__all__ = [
    "CapabilityThresholds",
    "EvidenceGrade",
    "grade_sources",
    "resolve_evidence_grade",
]
