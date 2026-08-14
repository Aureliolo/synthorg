"""Unit tests for turning published scores into capability rungs."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.providers.capability_sources.grading import (
    CapabilityThresholds,
    grade_sources,
    resolve_evidence_grade,
)
from synthorg.providers.capability_sources.models import (
    CapabilityAxis,
    CapabilityScore,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_FRESH = _NOW - timedelta(days=30)
_ANCIENT = _NOW - timedelta(days=1200)
_THRESHOLDS = CapabilityThresholds(
    expert_percentile=0.75,
    capable_percentile=0.35,
    max_age_days=730,
)


def _score(
    model: str,
    score: float,
    *,
    source: str = "source-a",
    axis: CapabilityAxis = "general",
    as_of: datetime = _FRESH,
) -> CapabilityScore:
    return CapabilityScore(
        source_label=NotBlankStr(source),
        model_identifier=NotBlankStr(model),
        axis=axis,
        score=score,
        as_of=as_of,
        ingested_at=_NOW,
    )


def _ladder(
    count: int,
    *,
    source: str = "source-a",
    axis: CapabilityAxis = "general",
    as_of: datetime = _FRESH,
) -> list[CapabilityScore]:
    """Build a cohort of *count* models evenly spread over the scale."""
    return [
        _score(
            f"m{i:03d}",
            i * (100.0 / (count - 1)),
            source=source,
            axis=axis,
            as_of=as_of,
        )
        for i in range(count)
    ]


class TestPercentileGrading:
    def test_the_cohort_splits_across_all_three_rungs(self) -> None:
        grades = grade_sources(_ladder(100), thresholds=_THRESHOLDS, now=_NOW)
        rungs = [g.capability for g in grades.values()]
        assert set(rungs) == {"basic", "capable", "expert"}

    def test_the_strongest_is_expert_and_the_weakest_is_basic(self) -> None:
        grades = grade_sources(_ladder(100), thresholds=_THRESHOLDS, now=_NOW)
        assert grades[("source-a", "m099")].capability == "expert"
        assert grades[("source-a", "m000")].capability == "basic"

    def test_rank_not_raw_score_decides_the_rung(self) -> None:
        """Two sources on different scales must grade the same shape.

        One publishes pass rates and another normalised ratings, so a raw
        threshold would grade the whole of one source below the whole of
        the other.
        """
        compressed = [
            _score(f"c{i}", 50.0 + i * 0.1, source="compressed") for i in range(20)
        ]
        spread = [_score(f"s{i}", i * 5.0, source="spread") for i in range(20)]
        grades = grade_sources([*compressed, *spread], thresholds=_THRESHOLDS, now=_NOW)
        assert grades[("compressed", "c19")].capability == "expert"
        assert grades[("spread", "s19")].capability == "expert"
        assert grades[("compressed", "c0")].capability == "basic"
        assert grades[("spread", "s0")].capability == "basic"

    def test_a_source_is_ranked_only_against_itself(self) -> None:
        strong = [_score(f"a{i}", 90.0 + i * 0.1, source="strong") for i in range(10)]
        weak = [_score(f"b{i}", i * 0.5, source="weak") for i in range(10)]
        grades = grade_sources([*strong, *weak], thresholds=_THRESHOLDS, now=_NOW)
        assert grades[("weak", "b9")].capability == "expert"
        assert grades[("strong", "a0")].capability == "basic"


class TestAxes:
    def test_the_weakest_axis_decides_the_rung(self) -> None:
        """A specialist must not inherit its speciality everywhere.

        Averaging a top coding score with a bottom reasoning score lands it
        mid-ladder, which describes neither half. Over-grading is the
        failure that routes work to a model that cannot do it, so the
        weakest measured axis decides.
        """
        rows = [
            *_ladder(20, axis="coding"),
            *_ladder(20, axis="reasoning"),
            _score("mixed", 100.0, axis="coding"),
            _score("mixed", 0.0, axis="reasoning"),
        ]
        grades = grade_sources(rows, thresholds=_THRESHOLDS, now=_NOW)
        grade = grades[("source-a", "mixed")]
        assert grade.capability == "basic"
        assert grade.deciding_axis == "reasoning"
        assert grade.axes_used == ("coding", "reasoning")

    def test_models_are_ranked_only_against_others_on_the_same_axis(self) -> None:
        """Ragged coverage must not put two axes in one cohort.

        A source measures whichever models it chose on whichever benchmarks
        it ran, so a reasoning-only model and a general-only model are not
        comparable and must not share a ranking.
        """
        rows = [*_ladder(10, axis="general"), *_ladder(10, axis="reasoning")]
        grades = grade_sources(rows, thresholds=_THRESHOLDS, now=_NOW)
        assert grades[("source-a", "m000")].cohort_size == 10

    def test_an_axis_too_thin_to_rank_in_grades_nothing(self) -> None:
        """One model on an axis is not the best at it, it is the only one."""
        rows = [
            *_ladder(20, axis="general"),
            _score("specialist", 100.0, axis="coding"),
        ]
        grades = grade_sources(rows, thresholds=_THRESHOLDS, now=_NOW)
        assert ("source-a", "specialist") not in grades

    def test_only_axes_that_graded_are_reported(self) -> None:
        rows = [
            *_ladder(20, axis="general"),
            _score("subject", 90.0, axis="general"),
            _score("subject", 5.0, axis="coding"),
        ]
        grades = grade_sources(rows, thresholds=_THRESHOLDS, now=_NOW)
        grade = grades[("source-a", "subject")]
        assert grade.axes_used == ("general",)
        assert grade.deciding_axis == "general"


class TestRecency:
    def test_a_stale_row_is_neither_graded_nor_counted(self) -> None:
        """An obsolete model must not pad the cohort it is ranked against."""
        rows = [*_ladder(20), _score("antique", 5.0, as_of=_ANCIENT)]
        grades = grade_sources(rows, thresholds=_THRESHOLDS, now=_NOW)
        assert ("source-a", "antique") not in grades
        assert grades[("source-a", "m000")].cohort_size == 20

    def test_a_source_with_only_stale_rows_grades_nothing(self) -> None:
        rows = [_score(f"old{i}", i * 10.0, as_of=_ANCIENT) for i in range(5)]
        assert grade_sources(rows, thresholds=_THRESHOLDS, now=_NOW) == {}


class TestProvenance:
    def test_every_grade_carries_where_and_when(self) -> None:
        grades = grade_sources(_ladder(20), thresholds=_THRESHOLDS, now=_NOW)
        grade = grades[("source-a", "m019")]
        assert grade.source_label == "source-a"
        assert grade.as_of == _FRESH
        assert grade.cohort_size == 20
        assert 0.0 <= grade.percentile <= 1.0

    def test_the_axis_a_rung_came_from_is_named(self) -> None:
        """A standing quoted without saying what it is a standing IN is the
        unattributed number this layer exists to replace."""
        grades = grade_sources(
            _ladder(20, axis="coding"), thresholds=_THRESHOLDS, now=_NOW
        )
        assert grades[("source-a", "m019")].deciding_axis == "coding"

    def test_as_of_is_the_newest_axis_measurement(self) -> None:
        older = _NOW - timedelta(days=200)
        rows = [
            *_ladder(20, axis="general"),
            *_ladder(20, axis="coding", as_of=older),
            _score("two-dates", 60.0, axis="coding", as_of=older),
            _score("two-dates", 60.0, axis="general", as_of=_FRESH),
        ]
        grades = grade_sources(rows, thresholds=_THRESHOLDS, now=_NOW)
        assert grades[("source-a", "two-dates")].as_of == _FRESH


class TestSingleModelCohort:
    def test_a_lone_model_is_not_crowned_by_default(self) -> None:
        """With no cohort there is no evidence of relative standing.

        Ranking one model against itself would put it at the top of its
        own list and grade it expert on nothing at all.
        """
        graded = grade_sources([_score("only", 99.0)], thresholds=_THRESHOLDS, now=_NOW)
        assert graded == {}


class TestDisagreement:
    def test_two_sources_disagreeing_resolve_to_the_lower_rung(self) -> None:
        """Over-grading routes work to a model that cannot do it.

        Under-grading only routes it to something better than needed, so
        the disagreement resolves downward.
        """
        rows = [
            *_ladder(20, source="generous"),
            _score("subject", 100.0, source="generous"),
            *_ladder(20, source="harsh"),
            _score("subject", 1.0, source="harsh"),
        ]
        grades = grade_sources(rows, thresholds=_THRESHOLDS, now=_NOW)
        resolved = resolve_evidence_grade(grades, model_identifier="subject")
        assert resolved is not None
        assert resolved.capability == "basic"

    def test_agreement_keeps_the_shared_rung(self) -> None:
        rows = [
            *_ladder(20, source="a"),
            _score("subject", 100.0, source="a"),
            *_ladder(20, source="b"),
            _score("subject", 100.0, source="b"),
        ]
        grades = grade_sources(rows, thresholds=_THRESHOLDS, now=_NOW)
        resolved = resolve_evidence_grade(grades, model_identifier="subject")
        assert resolved is not None
        assert resolved.capability == "expert"

    def test_one_source_alone_still_grades(self) -> None:
        """A source being down must not withdraw the other's evidence."""
        rows = [*_ladder(20, source="a"), _score("subject", 100.0, source="a")]
        grades = grade_sources(rows, thresholds=_THRESHOLDS, now=_NOW)
        resolved = resolve_evidence_grade(grades, model_identifier="subject")
        assert resolved is not None
        assert resolved.capability == "expert"
        assert resolved.source_label == "a"

    def test_a_model_no_source_measured_has_no_grade(self) -> None:
        grades = grade_sources(_ladder(20), thresholds=_THRESHOLDS, now=_NOW)
        assert resolve_evidence_grade(grades, model_identifier="unheard-of") is None


class TestThresholds:
    def test_percentiles_must_be_ordered(self) -> None:
        with pytest.raises(ValueError, match="capable_percentile"):
            CapabilityThresholds(
                expert_percentile=0.3,
                capable_percentile=0.6,
                max_age_days=730,
            )
