"""Tests for the task lifecycle state machine transitions."""

import pytest
import structlog

from ai_company.core.enums import TaskStatus
from ai_company.core.task_transitions import VALID_TRANSITIONS, validate_transition
from ai_company.observability.events.task import TASK_TRANSITION_INVALID

pytestmark = pytest.mark.timeout(30)

# ── Valid Transitions ─────────────────────────────────────────────


@pytest.mark.unit
class TestValidTransitions:
    """Test all valid state transitions per DESIGN_SPEC 6.1."""

    def test_created_to_assigned(self) -> None:
        validate_transition(TaskStatus.CREATED, TaskStatus.ASSIGNED)

    def test_assigned_to_in_progress(self) -> None:
        validate_transition(TaskStatus.ASSIGNED, TaskStatus.IN_PROGRESS)

    def test_assigned_to_blocked(self) -> None:
        validate_transition(TaskStatus.ASSIGNED, TaskStatus.BLOCKED)

    def test_assigned_to_cancelled(self) -> None:
        validate_transition(TaskStatus.ASSIGNED, TaskStatus.CANCELLED)

    def test_in_progress_to_in_review(self) -> None:
        validate_transition(TaskStatus.IN_PROGRESS, TaskStatus.IN_REVIEW)

    def test_in_progress_to_blocked(self) -> None:
        validate_transition(TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED)

    def test_in_progress_to_cancelled(self) -> None:
        validate_transition(TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED)

    def test_in_review_to_completed(self) -> None:
        validate_transition(TaskStatus.IN_REVIEW, TaskStatus.COMPLETED)

    def test_in_review_to_in_progress_rework(self) -> None:
        validate_transition(TaskStatus.IN_REVIEW, TaskStatus.IN_PROGRESS)

    def test_in_review_to_blocked(self) -> None:
        validate_transition(TaskStatus.IN_REVIEW, TaskStatus.BLOCKED)

    def test_in_review_to_cancelled(self) -> None:
        validate_transition(TaskStatus.IN_REVIEW, TaskStatus.CANCELLED)

    def test_blocked_to_assigned(self) -> None:
        validate_transition(TaskStatus.BLOCKED, TaskStatus.ASSIGNED)


# ── Invalid Transitions ──────────────────────────────────────────


@pytest.mark.unit
class TestInvalidTransitions:
    """Test that invalid transitions raise ValueError."""

    def test_created_to_completed_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid task status transition"):
            validate_transition(TaskStatus.CREATED, TaskStatus.COMPLETED)

    def test_created_to_in_progress_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid task status transition"):
            validate_transition(TaskStatus.CREATED, TaskStatus.IN_PROGRESS)

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

    def test_assigned_to_completed_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid task status transition"):
            validate_transition(TaskStatus.ASSIGNED, TaskStatus.COMPLETED)

    def test_blocked_to_completed_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid task status transition"):
            validate_transition(TaskStatus.BLOCKED, TaskStatus.COMPLETED)

    def test_in_progress_to_completed_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid task status transition"):
            validate_transition(TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED)

    def test_in_progress_to_assigned_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid task status transition"):
            validate_transition(TaskStatus.IN_PROGRESS, TaskStatus.ASSIGNED)

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
        """COMPLETED and CANCELLED must have no outgoing transitions."""
        assert VALID_TRANSITIONS[TaskStatus.COMPLETED] == frozenset()
        assert VALID_TRANSITIONS[TaskStatus.CANCELLED] == frozenset()

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
        assert events[0]["current_status"] == "created"
        assert events[0]["target_status"] == "completed"
