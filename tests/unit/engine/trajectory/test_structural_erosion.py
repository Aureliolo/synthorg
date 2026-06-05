"""Tests for structural erosion metrics."""

import pytest

from synthorg.engine.trajectory.structural_erosion import (
    compute_cyclomatic_complexity_delta,
    compute_structural_erosion_score,
    detect_dead_branches,
    detect_duplicated_blocks,
)
from synthorg.execution.turn import TurnRecord
from synthorg.providers.enums import FinishReason


def _make_turn(
    turn_number: int,
    tool_calls: tuple[str, ...] = (),
    fingerprints: tuple[str, ...] = (),
) -> TurnRecord:
    return TurnRecord(
        turn_number=turn_number,
        input_tokens=100,
        output_tokens=50,
        cost=0.01,
        finish_reason=FinishReason.STOP,
        tool_calls_made=tool_calls,
        tool_call_fingerprints=fingerprints,
    )


@pytest.mark.unit
class TestDetectDuplicatedBlocks:
    """detect_duplicated_blocks function."""

    def test_empty_turns(self) -> None:
        assert detect_duplicated_blocks(()) == 0.0

    def test_single_turn(self) -> None:
        turns = (_make_turn(1, ("read",), ("read:abc",)),)
        assert detect_duplicated_blocks(turns) == 0.0

    def test_no_duplicates(self) -> None:
        turns = (
            _make_turn(1, ("read",), ("read:abc",)),
            _make_turn(2, ("write",), ("write:def",)),
            _make_turn(3, ("grep",), ("grep:ghi",)),
        )
        assert detect_duplicated_blocks(turns) == 0.0

    def test_all_duplicates(self) -> None:
        turns = (
            _make_turn(1, ("read",), ("read:abc",)),
            _make_turn(2, ("read",), ("read:abc",)),
            _make_turn(3, ("read",), ("read:abc",)),
        )
        ratio = detect_duplicated_blocks(turns)
        # 2 out of 3 turns are duplicates.
        assert ratio == pytest.approx(2.0 / 3.0)

    def test_window_size(self) -> None:
        turns = tuple(_make_turn(i, ("read",), ("read:abc",)) for i in range(1, 20))
        ratio = detect_duplicated_blocks(turns, window_size=5)
        # Window of 5, all same fingerprints -> 4/5 duplicates.
        assert ratio == pytest.approx(4.0 / 5.0)


@pytest.mark.unit
class TestComputeCyclomaticComplexityDelta:
    """compute_cyclomatic_complexity_delta function."""

    def test_insufficient_data(self) -> None:
        turns = (
            _make_turn(1, ("read",), ("read:a",)),
            _make_turn(2, ("read",), ("read:b",)),
        )
        assert compute_cyclomatic_complexity_delta(turns) == 0.0

    def test_no_change(self) -> None:
        turns = (
            _make_turn(1, ("read",), ("read:a",)),
            _make_turn(2, ("write",), ("write:b",)),
            _make_turn(3, ("read",), ("read:c",)),
            _make_turn(4, ("write",), ("write:d",)),
        )
        # Each half has 2 unique fingerprints -> delta = 0.
        assert compute_cyclomatic_complexity_delta(turns) == 0.0

    def test_increasing_complexity(self) -> None:
        turns = (
            _make_turn(1, ("read",), ("read:a",)),
            _make_turn(2, ("read",), ("read:a",)),
            _make_turn(3, ("read",), ("read:b",)),
            _make_turn(4, ("write",), ("write:c",)),
            _make_turn(5, ("grep",), ("grep:d",)),
            _make_turn(6, ("edit",), ("edit:e",)),
        )
        delta = compute_cyclomatic_complexity_delta(turns)
        assert delta > 0.0
        assert delta <= 1.0


@pytest.mark.unit
class TestDetectDeadBranches:
    """detect_dead_branches function."""

    def test_insufficient_data(self) -> None:
        turns = (
            _make_turn(1, ("read",), ("read:a",)),
            _make_turn(2, ("write",), ("write:b",)),
        )
        assert detect_dead_branches(turns) == 0.0

    def test_all_consumed(self) -> None:
        turns = (
            _make_turn(1, ("read",), ("read:a",)),
            _make_turn(2, ("read",), ("read:b",)),
            _make_turn(3, ("read",), ("read:c",)),
        )
        # Each "read" is followed by another "read" -> consumed.
        assert detect_dead_branches(turns) == 0.0

    def test_dead_branch_last_turn(self) -> None:
        # Only non-last turns are checked, so the last turn's
        # calls are not counted as dead branches.
        turns = (
            _make_turn(1, ("read",), ("read:a",)),
            _make_turn(2, ("read",), ("read:b",)),
            _make_turn(3, ("write",), ("write:c",)),
        )
        # Turn 1: "read" consumed by turn 2 "read" -> OK.
        # Turn 2: "read" NOT consumed by turn 3 "write" -> dead.
        ratio = detect_dead_branches(turns)
        assert ratio > 0.0


@pytest.mark.unit
class TestComputeStructuralErosionScore:
    """compute_structural_erosion_score composite."""

    def test_empty_turns(self) -> None:
        assert compute_structural_erosion_score(()) == 0.0

    def test_score_bounded(self) -> None:
        turns = tuple(_make_turn(i, ("read",), ("read:abc",)) for i in range(1, 20))
        score = compute_structural_erosion_score(turns)
        assert 0.0 <= score <= 1.0

    def test_no_erosion(self) -> None:
        turns = (
            _make_turn(1, ("read",), ("read:a",)),
            _make_turn(2, ("write",), ("write:b",)),
            _make_turn(3, ("read",), ("read:c",)),
            _make_turn(4, ("write",), ("write:d",)),
            _make_turn(5, ("read",), ("read:e",)),
        )
        score = compute_structural_erosion_score(turns)
        # Distinct fingerprints, steady complexity -> low score.
        assert score < 0.5
