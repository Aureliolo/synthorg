"""Tests for ``evals.scoring.penalties`` and ``evals.scoring.aggregate``."""

import pytest
from pydantic import ValidationError as PydValidationError

from evals.scoring.aggregate import (
    GRADE_CEILING,
    GRADE_FLOOR,
    AggregationResult,
    PenaltyEntry,
    aggregate_brief_score,
)
from evals.scoring.penalties import (
    DEFAULT_PENALTY_TABLE,
    PENALTY_BUDGET_HARD_STOP,
    PENALTY_CAP_PER_CLASS,
    PENALTY_CLASS_BRIEF_WALL_CLOCK,
    PENALTY_FLOOR,
    PENALTY_STAGNATION_DETECTED,
    PENALTY_STAGNATION_TERMINATED,
    PenaltyTable,
)
from synthorg.observability.events.budget import BUDGET_HARD_STOP_EXCEEDED
from synthorg.observability.events.stagnation import (
    STAGNATION_DETECTED,
    STAGNATION_TERMINATED,
)


@pytest.mark.unit
def test_clean_run_has_no_deduction() -> None:
    result = aggregate_brief_score(
        grade=100, events_by_class={}, penalty_table=DEFAULT_PENALTY_TABLE
    )
    assert result.deduction == 0
    assert result.score == 100
    assert result.is_clean is True
    assert result.entries == ()


@pytest.mark.unit
def test_single_budget_hard_stop_applied_at_full_cost() -> None:
    result = aggregate_brief_score(
        grade=100,
        events_by_class={BUDGET_HARD_STOP_EXCEEDED: 1},
        penalty_table=DEFAULT_PENALTY_TABLE,
    )
    assert result.deduction == PENALTY_BUDGET_HARD_STOP
    assert result.score == 100 - PENALTY_BUDGET_HARD_STOP


@pytest.mark.unit
def test_stagnation_multiple_events_capped_per_class() -> None:
    # 2 * detected (10) + 1 * terminated (25) = 45 if uncapped.
    # detected_raw = 20 -> applied 20 (under cap)
    # terminated_raw = 25 -> applied 25 (under cap)
    # total = 45 (no per-class cap kicks in because each class is below cap)
    result = aggregate_brief_score(
        grade=100,
        events_by_class={
            STAGNATION_DETECTED: 2,
            STAGNATION_TERMINATED: 1,
        },
        penalty_table=DEFAULT_PENALTY_TABLE,
    )
    expected = (2 * PENALTY_STAGNATION_DETECTED) + (1 * PENALTY_STAGNATION_TERMINATED)
    assert result.deduction == expected
    assert result.score == 100 - expected


@pytest.mark.unit
def test_per_class_cap_clamps_runaway_event_count() -> None:
    # 5 detected events * 10 = 50, exceeds cap of 40 -> applied 40.
    result = aggregate_brief_score(
        grade=100,
        events_by_class={STAGNATION_DETECTED: 5},
        penalty_table=DEFAULT_PENALTY_TABLE,
    )
    assert result.deduction == PENALTY_CAP_PER_CLASS
    assert result.score == 100 - PENALTY_CAP_PER_CLASS


@pytest.mark.unit
def test_score_floor_clamps_negatives_to_zero() -> None:
    # Grade 10, hard-stop penalty 30 -> final would be -20; clamped to floor.
    result = aggregate_brief_score(
        grade=10,
        events_by_class={BUDGET_HARD_STOP_EXCEEDED: 1},
        penalty_table=DEFAULT_PENALTY_TABLE,
    )
    assert result.score == PENALTY_FLOOR


@pytest.mark.unit
def test_untracked_event_constant_is_silently_ignored() -> None:
    result = aggregate_brief_score(
        grade=100,
        events_by_class={"some.unrelated.event": 99},
        penalty_table=DEFAULT_PENALTY_TABLE,
    )
    assert result.deduction == 0
    assert result.score == 100


@pytest.mark.unit
def test_synthetic_wall_clock_class_resolves() -> None:
    # The runner emits a synthetic wall-clock-over class (not a real event).
    result = aggregate_brief_score(
        grade=100,
        events_by_class={PENALTY_CLASS_BRIEF_WALL_CLOCK: 1},
        penalty_table=DEFAULT_PENALTY_TABLE,
    )
    assert result.deduction > 0


@pytest.mark.unit
def test_grade_must_be_in_range() -> None:
    with pytest.raises(ValueError, match="outside"):
        aggregate_brief_score(
            grade=GRADE_CEILING + 1,
            events_by_class={},
            penalty_table=DEFAULT_PENALTY_TABLE,
        )
    with pytest.raises(ValueError, match="outside"):
        aggregate_brief_score(
            grade=GRADE_FLOOR - 1,
            events_by_class={},
            penalty_table=DEFAULT_PENALTY_TABLE,
        )


@pytest.mark.unit
def test_entries_are_sorted_for_determinism() -> None:
    result = aggregate_brief_score(
        grade=100,
        events_by_class={
            STAGNATION_TERMINATED: 1,
            BUDGET_HARD_STOP_EXCEEDED: 1,
            STAGNATION_DETECTED: 1,
        },
        penalty_table=DEFAULT_PENALTY_TABLE,
    )
    constants = [e.event_constant for e in result.entries]
    assert constants == sorted(constants)


@pytest.mark.unit
def test_penalty_entry_rejects_inconsistent_raw_points() -> None:
    """PenaltyEntry refuses raw != points_per_event * count."""
    with pytest.raises(PydValidationError, match="does not match"):
        PenaltyEntry(
            event_constant="x",
            count=3,
            points_per_event=10,
            raw_points=25,  # should be 30
            applied_points=25,
        )


@pytest.mark.unit
def test_penalty_entry_rejects_applied_exceeding_raw() -> None:
    """PenaltyEntry refuses applied > raw (cap goes one way only)."""
    with pytest.raises(PydValidationError, match="exceeds"):
        PenaltyEntry(
            event_constant="x",
            count=2,
            points_per_event=10,
            raw_points=20,
            applied_points=30,
        )


@pytest.mark.unit
def test_aggregation_result_rejects_inconsistent_score() -> None:
    """AggregationResult refuses score != max(grade - deduction, floor)."""
    with pytest.raises(PydValidationError, match="does not match"):
        AggregationResult(
            grade=80,
            deduction=10,
            score=60,  # should be 70
            entries=(),
        )


@pytest.mark.unit
def test_aggregate_brief_score_rejects_negative_count() -> None:
    """aggregate_brief_score refuses negative per-class counts; a "negative
    penalty" would silently become a bonus and corrupt the score."""
    with pytest.raises(ValueError, match="must be >= 0"):
        aggregate_brief_score(
            grade=80,
            events_by_class={STAGNATION_DETECTED: -1},
            penalty_table=DEFAULT_PENALTY_TABLE,
        )


@pytest.mark.unit
def test_penalty_table_rejects_negative_points() -> None:
    """PenaltyTable refuses points_per_event values < 0; a per-event "penalty"
    that adds points is a bonus, not a penalty, and is rejected."""
    with pytest.raises(PydValidationError, match="must be >= 0"):
        PenaltyTable(points_per_event={"x.event": -5})


@pytest.mark.unit
def test_penalty_table_rejects_floor_above_grade_ceiling() -> None:
    """PenaltyTable refuses floor > GRADE_CEILING; the floor must live inside
    the grade domain or downstream math produces out-of-range scores."""
    with pytest.raises(PydValidationError):
        PenaltyTable(floor=GRADE_CEILING + 1)
