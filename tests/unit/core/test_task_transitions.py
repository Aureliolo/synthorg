"""Tests for the task lifecycle state machine transitions."""

from unittest.mock import patch

import pytest
import structlog

from synthorg.core.enums import TaskStatus
from synthorg.core.task_transitions import VALID_TRANSITIONS, validate_transition
from synthorg.observability.events.task import TASK_TRANSITION_INVALID

pytestmark = pytest.mark.timeout(30)

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
            (TaskStatus.BLOCKED, TaskStatus.ASSIGNED),
            (TaskStatus.FAILED, TaskStatus.ASSIGNED),
            (TaskStatus.INTERRUPTED, TaskStatus.ASSIGNED),
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

    def test_failed_is_non_terminal(self) -> None:
        """FAILED has outgoing transitions (reassignment)."""
        assert len(VALID_TRANSITIONS[TaskStatus.FAILED]) > 0

    def test_interrupted_is_non_terminal(self) -> None:
        """INTERRUPTED has outgoing transitions (reassignment on restart)."""
        assert len(VALID_TRANSITIONS[TaskStatus.INTERRUPTED]) > 0

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


# ── Guard / missing entry edge cases ────────────────────────────


@pytest.mark.unit
class TestTransitionGuardEdgeCases:
    def test_module_level_guard_detects_missing_status(self) -> None:
        """The module-level guard raises ValueError for missing entries.

        We verify by checking the guard logic directly — adding a new
        member at runtime would be impractical.
        """
        missing = set(TaskStatus) - set(VALID_TRANSITIONS)
        assert missing == set(), "Module guard should have caught this at import time"

    def test_validate_transition_with_missing_entry(self) -> None:
        """validate_transition raises ValueError when current status is absent."""
        with (
            patch.dict(
                "synthorg.core.task_transitions.VALID_TRANSITIONS",
                clear=True,
            ),
            pytest.raises(ValueError, match="has no entry"),
        ):
            validate_transition(TaskStatus.CREATED, TaskStatus.ASSIGNED)
