# module-kind: code
"""Turn published scores into capability rungs.

A rung is decided by a model's **rank within one axis of the source that
measured it**, never by its raw score. Sources publish on their own scales
(a pass rate, a normalised rating, a resolve percentage), so a shared
numeric threshold would grade the whole of one source below the whole of
another while measuring nothing about either. A percentile is scale-free,
which means one pair of settings serves every source including ones added
later.

**Per axis, not per model.** A source measures whichever models it chose on
whichever benchmarks it ran, so its coverage is ragged: in the shipped
snapshot 51 models carry one axis, 45 carry two and 39 carry three.
Averaging a model's axes into one number and ranking that once compares a
model measured only on reasoning against one measured only on general,
which is not a comparison at all. It also hides the models that need
telling apart most: 35 of the 84 multi-axis models rank more than 40
percentile points apart between their own axes, one of them sitting at the
4th percentile on general and the 96th on reasoning. A single averaged rung
describes neither half.

So each axis is ranked in its own cohort, and a model's rung is the LOWEST
its axes support, with ``deciding_axis`` naming the one that produced it.
Lowest rather than mean for the same reason the cross-source rule is
lowest: over-grading routes work to a model that cannot do it, while
under-grading routes it to something better than it needed, and only one of
those is a failure. A specialist therefore lands on the rung its weakest
measured axis supports instead of inheriting its speciality everywhere.

The cohort a model ranks against is that axis's own recently-measured
population. Ranking against everything a source ever published would let a
model look strong purely because the list behind it is full of models
nobody would configure today.

Where two sources disagree the LOWER rung wins, by the same argument.
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
#: two-model list is the better of two, not an expert. An axis with a
#: cohort this small grades nothing, and a model measured only on that axis
#: keeps its heuristic rung.
_MIN_COHORT: Final[int] = 5


class CapabilityThresholds(BaseModel):
    """Where the rung boundaries sit on the within-axis percentile.

    Attributes:
        expert_percentile: At or above this rank fraction a model is
            ``expert``.
        capable_percentile: At or above this rank fraction (and below the
            expert line) a model is ``capable``; below it, ``basic``.
        max_age_days: How long a row keeps counting after its source was
            last read. Sources do not date individual measurements, so
            ``as_of`` records the read rather than the run; a row past this
            age neither grades its model nor pads the cohort, which is what
            retires a feed that has quietly stopped answering.
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
        capability: The rung this source's measurements put the model on,
            which is the lowest its measured axes support.
        source_label: Which source measured it.
        model_identifier: The identifier the source used, verbatim.
        deciding_axis: The axis the rung came from. Named because
            ``percentile`` and ``cohort_size`` describe THAT axis, and a
            standing quoted without saying what it is a standing in is the
            kind of unattributed number this layer exists to replace.
        percentile: Where the model sits in the deciding axis's cohort.
        cohort_size: How many models the deciding axis ranked it against. A
            grade from a small cohort is weaker evidence and says so.
        axes_used: Every axis that graded it, so a coding-only measurement
            is not mistaken for an all-round one.
        as_of: The newest measurement behind any of those axes.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    capability: CapabilityLevel = Field(description="Rung the evidence supports")
    source_label: NotBlankStr = Field(description="Source that measured it")
    model_identifier: NotBlankStr = Field(description="Identifier the source used")
    deciding_axis: CapabilityAxis = Field(description="Axis the rung came from")
    percentile: float = Field(ge=0.0, le=1.0, description="Rank within that axis")
    cohort_size: int = Field(ge=0, description="Models ranked against on that axis")
    axes_used: tuple[CapabilityAxis, ...] = Field(description="Axes that graded it")
    as_of: AwareDatetime = Field(description="When the source measured it")


class _AxisMeasurement(BaseModel):
    """One model's collapsed measurement on one axis."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    model_identifier: NotBlankStr
    score: float
    as_of: AwareDatetime


class _AxisGrade(BaseModel):
    """What one axis concluded about one model."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    axis: CapabilityAxis
    capability: CapabilityLevel
    percentile: float
    cohort_size: int
    as_of: AwareDatetime


def _collapse_by_axis(
    rows: Iterable[CapabilityScore],
) -> dict[CapabilityAxis, dict[str, _AxisMeasurement]]:
    """Reduce one source's rows to one measurement per model per axis.

    Several benchmarks can land on the same axis, and those are averaged:
    within one axis they are asking the same kind of question, which is
    what makes the mean meaningful here and meaningless across axes.

    Returns:
        Per axis, the measurement for each model it covers.
    """
    grouped: dict[tuple[CapabilityAxis, str], list[CapabilityScore]] = {}
    for row in rows:
        grouped.setdefault((row.axis, str(row.model_identifier)), []).append(row)
    by_axis: dict[CapabilityAxis, dict[str, _AxisMeasurement]] = {}
    for (axis, identifier), scores in grouped.items():
        by_axis.setdefault(axis, {})[identifier] = _AxisMeasurement(
            model_identifier=NotBlankStr(identifier),
            score=sum(s.score for s in scores) / len(scores),
            as_of=max(s.as_of for s in scores),
        )
    return by_axis


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


def _grade_one_axis(
    axis: CapabilityAxis,
    measured: Mapping[str, _AxisMeasurement],
    thresholds: CapabilityThresholds,
) -> dict[str, _AxisGrade]:
    """Rank one axis's cohort and grade every model in it.

    Returns:
        The per-model verdict for this axis, empty when its cohort is too
        small to rank within.
    """
    cohort_size = len(measured)
    if cohort_size < _MIN_COHORT:
        return {}
    ordered = sorted(
        measured.values(),
        key=lambda m: (m.score, m.model_identifier),
    )
    # Rank is the fraction of the cohort a model stands at or above, so the
    # weakest sits at 0.0 and the strongest just below 1.0 rather than at it:
    # nothing outranks the whole cohort including itself.
    graded: dict[str, _AxisGrade] = {}
    for index, entry in enumerate(ordered):
        percentile = index / cohort_size
        graded[str(entry.model_identifier)] = _AxisGrade(
            axis=axis,
            capability=_capability_for(percentile, thresholds),
            percentile=percentile,
            cohort_size=cohort_size,
            as_of=entry.as_of,
        )
    return graded


def _grade_one_source(
    source_label: str,
    rows: Sequence[CapabilityScore],
    thresholds: CapabilityThresholds,
) -> dict[tuple[str, str], EvidenceGrade]:
    """Grade every model one source measured, axis by axis.

    Returns:
        The grades for this source, keyed by ``(source, identifier)``.
    """
    per_model: dict[str, list[_AxisGrade]] = {}
    for axis, measured in _collapse_by_axis(rows).items():
        for identifier, axis_grade in _grade_one_axis(
            axis, measured, thresholds
        ).items():
            per_model.setdefault(identifier, []).append(axis_grade)

    graded: dict[tuple[str, str], EvidenceGrade] = {}
    for identifier, axis_grades in per_model.items():
        weakest = min(
            axis_grades,
            key=lambda g: (capability_rank(g.capability), g.percentile),
        )
        graded[(source_label, identifier)] = EvidenceGrade(
            capability=weakest.capability,
            source_label=NotBlankStr(source_label),
            model_identifier=NotBlankStr(identifier),
            deciding_axis=weakest.axis,
            percentile=weakest.percentile,
            cohort_size=weakest.cohort_size,
            axes_used=tuple(sorted({g.axis for g in axis_grades})),
            as_of=max(g.as_of for g in axis_grades),
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
        A source whose every recent axis cohort is too small to rank within
        grades nothing, and contributes no entries.
    """
    cutoff = now - timedelta(days=thresholds.max_age_days)
    by_source: dict[str, list[CapabilityScore]] = {}
    for row in scores:
        if row.as_of < cutoff:
            continue
        by_source.setdefault(str(row.source_label), []).append(row)

    graded: dict[tuple[str, str], EvidenceGrade] = {}
    for source_label, rows in by_source.items():
        graded.update(_grade_one_source(source_label, rows, thresholds))
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
        rung an operator sees is one a named source actually produced, on a
        named axis.
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
