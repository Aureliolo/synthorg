"""Tests for the task lifecycle state machine transitions."""

import pytest
import structlog

from synthorg.core.enums import TaskStatus
from synthorg.core.task_transitions import (
    VALID_TRANSITIONS,
    transition_path,
    validate_transition,
)
from synthorg.observability.events.task import TASK_TRANSITION_INVALID

# ── Valid Transitions ─────────────────────────────────────────────


@pytest.mark.unit
class TestValidTransitions:
    """Test all valid state transitions per DESIGN_SPEC 6.1."""

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (TaskStatus.CREATED, TaskStatus.ASSIGNED),
            (TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS),
            (TaskStatus.ASSIGNED, TaskStatus.BLOCKED),
            (TaskStatus.ASSIGNED, TaskStatus.CANCELLED),
            (TaskStatus.ASSIGNED, TaskStatus.FAILED),
            (TaskStatus.IN_PROGRESS, TaskStatus.IN_REVIEW),
            (TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED),
            (TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED),
            (TaskStatus.IN_PROGRESS, TaskStatus.FAILED),
            (TaskStatus.IN_REVIEW, TaskStatus.COMPLETED),
            (TaskStatus.IN_REVIEW, TaskStatus.IN_PROGRESS),
            (TaskStatus.IN_REVIEW, TaskStatus.BLOCKED),
            (TaskStatus.IN_REVIEW, TaskStatus.CANCELLED),
            (TaskStatus.ASSIGNED, TaskStatus.INTERRUPTED),
            (TaskStatus.IN_PROGRESS, TaskStatus.INTERRUPTED),
            (TaskStatus.ASSIGNED, TaskStatus.SUSPENDED),
            (TaskStatus.IN_PROGRESS, TaskStatus.SUSPENDED),
            (TaskStatus.BLOCKED, TaskStatus.ASSIGNED),
            (TaskStatus.FAILED, TaskStatus.ASSIGNED),
            (TaskStatus.INTERRUPTED, TaskStatus.ASSIGNED),
            (TaskStatus.SUSPENDED, TaskStatus.ASSIGNED),
            # REJECTED and AUTH_REQUIRED transitions
            (TaskStatus.CREATED, TaskStatus.REJECTED),
            (TaskStatus.ASSIGNED, TaskStatus.AUTH_REQUIRED),
            (TaskStatus.IN_PROGRESS, TaskStatus.AUTH_REQUIRED),
            (TaskStatus.AUTH_REQUIRED, TaskStatus.ASSIGNED),
            (TaskStatus.AUTH_REQUIRED, TaskStatus.CANCELLED),
        ],
        ids=lambda p: p.value if isinstance(p, TaskStatus) else str(p),
    )
    def test_valid_transition(self, source: TaskStatus, target: TaskStatus) -> None:
        validate_transition(source, target)


# ── Invalid Transitions ──────────────────────────────────────────


@pytest.mark.unit
class TestInvalidTransitions:
    """Test that invalid transitions raise ValueError."""

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (TaskStatus.CREATED, TaskStatus.COMPLETED),
            (TaskStatus.CREATED, TaskStatus.IN_PROGRESS),
            (TaskStatus.ASSIGNED, TaskStatus.COMPLETED),
            (TaskStatus.BLOCKED, TaskStatus.COMPLETED),
            (TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED),
            (TaskStatus.IN_PROGRESS, TaskStatus.ASSIGNED),
            (TaskStatus.FAILED, TaskStatus.COMPLETED),
            (TaskStatus.FAILED, TaskStatus.IN_PROGRESS),
            (TaskStatus.INTERRUPTED, TaskStatus.COMPLETED),
            (TaskStatus.INTERRUPTED, TaskStatus.IN_PROGRESS),
            (TaskStatus.SUSPENDED, TaskStatus.COMPLETED),
            (TaskStatus.SUSPENDED, TaskStatus.IN_PROGRESS),
            (TaskStatus.CREATED, TaskStatus.SUSPENDED),
            (TaskStatus.IN_REVIEW, TaskStatus.SUSPENDED),
            # REJECTED and AUTH_REQUIRED invalid transitions
            (TaskStatus.REJECTED, TaskStatus.ASSIGNED),
            (TaskStatus.REJECTED, TaskStatus.CREATED),
            (TaskStatus.AUTH_REQUIRED, TaskStatus.COMPLETED),
            (TaskStatus.AUTH_REQUIRED, TaskStatus.IN_PROGRESS),
            (TaskStatus.CREATED, TaskStatus.AUTH_REQUIRED),
        ],
        ids=lambda p: p.value if isinstance(p, TaskStatus) else str(p),
    )
    def test_invalid_transition_rejected(
        self, source: TaskStatus, target: TaskStatus
    ) -> None:
        with pytest.raises(ValueError, match="Invalid task status transition"):
            validate_transition(source, target)

    def test_completed_to_any_rejected(self) -> None:
        for target in TaskStatus:
            if target is TaskStatus.COMPLETED:
                continue
            with pytest.raises(ValueError, match="Invalid task status transition"):
                validate_transition(TaskStatus.COMPLETED, target)

    def test_cancelled_to_any_rejected(self) -> None:
        for target in TaskStatus:
            if target is TaskStatus.CANCELLED:
                continue
            with pytest.raises(ValueError, match="Invalid task status transition"):
                validate_transition(TaskStatus.CANCELLED, target)

    def test_rejected_to_any_rejected(self) -> None:
        for target in TaskStatus:
            if target is TaskStatus.REJECTED:
                continue
            with pytest.raises(ValueError, match="Invalid task status transition"):
                validate_transition(TaskStatus.REJECTED, target)

    def test_error_message_includes_allowed(self) -> None:
        with pytest.raises(ValueError, match="Allowed from 'created'"):
            validate_transition(TaskStatus.CREATED, TaskStatus.COMPLETED)


# ── Transition Map Completeness ──────────────────────────────────


@pytest.mark.unit
class TestTransitionMapCompleteness:
    """Verify the transition map covers all TaskStatus members."""

    def test_all_statuses_have_entry(self) -> None:
        """Every TaskStatus member must have an entry in VALID_TRANSITIONS."""
        for status in TaskStatus:
            assert status in VALID_TRANSITIONS, (
                f"{status.value!r} missing from VALID_TRANSITIONS"
            )

    def test_terminal_states_have_empty_transitions(self) -> None:
        """COMPLETED, CANCELLED, and REJECTED must have no outgoing transitions."""
        assert VALID_TRANSITIONS[TaskStatus.COMPLETED] == frozenset()
        assert VALID_TRANSITIONS[TaskStatus.CANCELLED] == frozenset()
        assert VALID_TRANSITIONS[TaskStatus.REJECTED] == frozenset()

    def test_auth_required_is_non_terminal(self) -> None:
        """AUTH_REQUIRED has outgoing transitions (approved or denied)."""
        assert len(VALID_TRANSITIONS[TaskStatus.AUTH_REQUIRED]) > 0

    def test_auth_required_transitions(self) -> None:
        """AUTH_REQUIRED can transition to ASSIGNED or CANCELLED."""
        assert VALID_TRANSITIONS[TaskStatus.AUTH_REQUIRED] == frozenset(
            {TaskStatus.ASSIGNED, TaskStatus.CANCELLED}
        )

    def test_failed_is_non_terminal(self) -> None:
        """FAILED has outgoing transitions (reassignment)."""
        assert len(VALID_TRANSITIONS[TaskStatus.FAILED]) > 0

    def test_interrupted_is_non_terminal(self) -> None:
        """INTERRUPTED has outgoing transitions (reassignment on restart)."""
        assert len(VALID_TRANSITIONS[TaskStatus.INTERRUPTED]) > 0

    def test_suspended_is_non_terminal(self) -> None:
        """SUSPENDED has outgoing transitions (resume from checkpoint)."""
        assert len(VALID_TRANSITIONS[TaskStatus.SUSPENDED]) > 0

    def test_suspended_has_single_outgoing(self) -> None:
        """SUSPENDED can only transition to ASSIGNED (resume)."""
        assert VALID_TRANSITIONS[TaskStatus.SUSPENDED] == frozenset(
            {TaskStatus.ASSIGNED}
        )

    def test_all_targets_are_valid_statuses(self) -> None:
        """Every target in the transition map must be a valid TaskStatus."""
        for source, targets in VALID_TRANSITIONS.items():
            for target in targets:
                assert isinstance(target, TaskStatus), (
                    f"Invalid target {target!r} from {source.value!r}"
                )

    def test_no_self_transitions(self) -> None:
        """No status should transition to itself."""
        for source, targets in VALID_TRANSITIONS.items():
            assert source not in targets, f"{source.value!r} has a self-transition"


# ── Logging tests ─────────────────────────────────────────────────


@pytest.mark.unit
class TestTransitionLogging:
    def test_invalid_transition_emits_warning(self) -> None:
        with (
            structlog.testing.capture_logs() as cap,
            pytest.raises(ValueError, match="Invalid task status"),
        ):
            validate_transition(TaskStatus.CREATED, TaskStatus.COMPLETED)
        events = [e for e in cap if e.get("event") == TASK_TRANSITION_INVALID]
        assert len(events) == 1
        # Generic ``current_state``/``target_state`` keys replace the old
        # ``current_status``/``target_status`` names so Kanban columns,
        # sprint phases, and client-request statuses all log with the
        # same field shape in the shared StateMachine helper.
        assert events[0]["current_state"] == "created"
        assert events[0]["target_state"] == "completed"


# ── Guard / missing entry edge cases ────────────────────────────


@pytest.mark.unit
class TestTransitionGuardEdgeCases:
    def test_module_level_guard_detects_missing_status(self) -> None:
        """The module-level guard raises ValueError for missing entries.

        We verify by checking the guard logic directly -- adding a new
        member at runtime would be impractical.
        """
        missing = set(TaskStatus) - set(VALID_TRANSITIONS)
        assert missing == set(), "Module guard should have caught this at import time"

    def test_validate_transition_with_missing_entry(self) -> None:
        """``StateMachine.validate`` raises ValueError when state is absent.

        Patching ``VALID_TRANSITIONS`` via ``patch.dict`` no longer
        changes the module-level ``StateMachine`` behaviour because
        the helper deep-copies + wraps the table in
        ``MappingProxyType`` at construction (per the CLAUDE.md
        immutability convention for non-Pydantic registries). We
        therefore reproduce the scenario by building a fresh
        ``StateMachine`` with an empty table and calling ``validate``
        directly -- this still exercises the missing-entry guard.
        """
        from synthorg.core.state_machine import StateMachine
        from synthorg.observability.events.task import (
            TASK_TRANSITION_CONFIG_ERROR,
        )

        empty_machine: StateMachine[TaskStatus] = StateMachine(
            {},
            name="task_status",
            invalid_event=TASK_TRANSITION_INVALID,
            config_event=TASK_TRANSITION_CONFIG_ERROR,
        )
        with pytest.raises(ValueError, match="has no entry"):
            empty_machine.validate(TaskStatus.CREATED, TaskStatus.ASSIGNED)


@pytest.mark.unit
class TestTransitionPath:
    """``transition_path`` walks the real task lifecycle."""

    def test_same_status_is_empty(self) -> None:
        assert transition_path(TaskStatus.IN_PROGRESS, TaskStatus.IN_PROGRESS) == ()

    def test_created_to_completed_routes_through_review(self) -> None:
        # A fully-completed coordination on a freshly CREATED parent:
        # COMPLETED is only reachable via IN_REVIEW.
        assert transition_path(TaskStatus.CREATED, TaskStatus.COMPLETED) == (
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.IN_REVIEW,
            TaskStatus.COMPLETED,
        )

    def test_created_to_failed_is_shortest(self) -> None:
        # ASSIGNED can fail directly, so the shortest route skips
        # IN_PROGRESS: CREATED -> ASSIGNED -> FAILED.
        assert transition_path(TaskStatus.CREATED, TaskStatus.FAILED) == (
            TaskStatus.ASSIGNED,
            TaskStatus.FAILED,
        )

    def test_in_progress_to_failed_single_hop(self) -> None:
        assert transition_path(TaskStatus.IN_PROGRESS, TaskStatus.FAILED) == (
            TaskStatus.FAILED,
        )

    def test_terminal_source_is_unreachable(self) -> None:
        assert transition_path(TaskStatus.COMPLETED, TaskStatus.FAILED) is None

    def test_every_hop_is_individually_valid(self) -> None:
        path = transition_path(TaskStatus.CREATED, TaskStatus.COMPLETED)
        assert path is not None
        cursor = TaskStatus.CREATED
        for hop in path:
            validate_transition(cursor, hop)  # raises if any hop illegal
            cursor = hop
