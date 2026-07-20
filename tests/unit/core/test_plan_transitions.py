"""Tests for the plan lifecycle state machine transitions."""

import pytest

from synthorg.core.plan_enums import (
    REWORKABLE_STATUSES,
    TERMINAL_STATUSES,
    PlanStatus,
)
from synthorg.core.plan_transitions import (
    VALID_TRANSITIONS,
    transition_path,
    validate_transition,
)


@pytest.mark.unit
class TestValidTransitions:
    """Every transition the plan lifecycle accepts."""

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            # Authoring: the greenlight shell is filled by the decomposer.
            (PlanStatus.PLANNING, PlanStatus.DRAFT),
            (PlanStatus.PLANNING, PlanStatus.PENDING_REVIEW),
            # Review round-trip.
            (PlanStatus.DRAFT, PlanStatus.PENDING_REVIEW),
            (PlanStatus.PENDING_REVIEW, PlanStatus.DRAFT),
            (PlanStatus.PENDING_REVIEW, PlanStatus.APPROVED),
            (PlanStatus.PENDING_REVIEW, PlanStatus.REJECTED),
            # Execution: approval dispatches the plan, and its items roll up.
            (PlanStatus.APPROVED, PlanStatus.EXECUTING),
            (PlanStatus.EXECUTING, PlanStatus.COMPLETED),
            # A replan retires the current revision at any live stage.
            (PlanStatus.DRAFT, PlanStatus.SUPERSEDED),
            (PlanStatus.PENDING_REVIEW, PlanStatus.SUPERSEDED),
            (PlanStatus.APPROVED, PlanStatus.SUPERSEDED),
            (PlanStatus.EXECUTING, PlanStatus.SUPERSEDED),
            # Planning itself can break before a plan exists.
            (PlanStatus.PLANNING, PlanStatus.FAILED),
        ],
        ids=lambda p: p.value if isinstance(p, PlanStatus) else str(p),
    )
    def test_valid_transition(self, source: PlanStatus, target: PlanStatus) -> None:
        validate_transition(source, target)


@pytest.mark.unit
class TestInvalidTransitions:
    """Transitions the plan lifecycle must refuse."""

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            # Execution requires an approval decision first.
            (PlanStatus.PENDING_REVIEW, PlanStatus.EXECUTING),
            (PlanStatus.DRAFT, PlanStatus.EXECUTING),
            # Completion requires the work to have actually run.
            (PlanStatus.APPROVED, PlanStatus.COMPLETED),
            (PlanStatus.PENDING_REVIEW, PlanStatus.COMPLETED),
            # A rejected plan is closed; a fresh plan is a new record.
            (PlanStatus.REJECTED, PlanStatus.DRAFT),
            (PlanStatus.REJECTED, PlanStatus.APPROVED),
            # A completed plan cannot re-enter execution.
            (PlanStatus.COMPLETED, PlanStatus.EXECUTING),
        ],
        ids=lambda p: p.value if isinstance(p, PlanStatus) else str(p),
    )
    def test_invalid_transition(self, source: PlanStatus, target: PlanStatus) -> None:
        with pytest.raises(ValueError, match="Invalid plan status transition"):
            validate_transition(source, target)

    @pytest.mark.parametrize(
        "terminal",
        [
            PlanStatus.COMPLETED,
            PlanStatus.REJECTED,
            PlanStatus.SUPERSEDED,
            PlanStatus.FAILED,
        ],
        ids=lambda p: p.value,
    )
    def test_terminal_has_no_outgoing_transitions(self, terminal: PlanStatus) -> None:
        assert VALID_TRANSITIONS[terminal] == frozenset()


@pytest.mark.unit
class TestLifecycleCoverage:
    """The table and the status partitions must stay coherent."""

    def test_every_status_has_an_entry(self) -> None:
        assert set(VALID_TRANSITIONS) == set(PlanStatus)

    def test_approved_is_no_longer_terminal(self) -> None:
        """APPROVED dispatches into execution, so it has outgoing hops."""
        assert PlanStatus.APPROVED not in TERMINAL_STATUSES
        assert VALID_TRANSITIONS[PlanStatus.APPROVED] != frozenset()

    def test_completed_is_terminal(self) -> None:
        assert PlanStatus.COMPLETED in TERMINAL_STATUSES

    def test_terminal_statuses_match_the_table(self) -> None:
        table_terminal = {s for s, t in VALID_TRANSITIONS.items() if not t}
        assert table_terminal == set(TERMINAL_STATUSES)

    def test_execution_statuses_are_not_reworkable(self) -> None:
        """An operator reworks a plan under review, never one mid-flight."""
        assert PlanStatus.EXECUTING not in REWORKABLE_STATUSES
        assert PlanStatus.COMPLETED not in REWORKABLE_STATUSES


@pytest.mark.unit
class TestTransitionPath:
    """Multi-hop pathing used to advance a plan to a rolled-up status."""

    def test_path_from_approved_to_completed_routes_through_executing(self) -> None:
        assert transition_path(PlanStatus.APPROVED, PlanStatus.COMPLETED) == (
            PlanStatus.EXECUTING,
            PlanStatus.COMPLETED,
        )

    def test_path_to_same_status_is_empty(self) -> None:
        assert transition_path(PlanStatus.EXECUTING, PlanStatus.EXECUTING) == ()

    def test_path_from_terminal_is_none(self) -> None:
        assert transition_path(PlanStatus.COMPLETED, PlanStatus.EXECUTING) is None
