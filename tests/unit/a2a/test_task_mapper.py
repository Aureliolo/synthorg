"""Tests for TaskStatus <-> A2ATaskState bidirectional mapping."""

import pytest

from synthorg.a2a.models import A2ATaskState
from synthorg.a2a.task_mapper import from_a2a, to_a2a
from synthorg.core.task_enums import TaskStatus


class TestToA2A:
    """Internal TaskStatus -> A2ATaskState mapping."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("internal", "expected"),
        [
            (TaskStatus.CREATED, A2ATaskState.SUBMITTED),
            (TaskStatus.ASSIGNED, A2ATaskState.WORKING),
            (TaskStatus.IN_PROGRESS, A2ATaskState.WORKING),
            (TaskStatus.IN_REVIEW, A2ATaskState.WORKING),
            (TaskStatus.COMPLETED, A2ATaskState.COMPLETED),
            (TaskStatus.BLOCKED, A2ATaskState.INPUT_REQUIRED),
            (TaskStatus.SUSPENDED, A2ATaskState.INPUT_REQUIRED),
            (TaskStatus.FAILED, A2ATaskState.FAILED),
            (TaskStatus.INTERRUPTED, A2ATaskState.FAILED),
            (TaskStatus.CANCELLED, A2ATaskState.CANCELED),
            (TaskStatus.REJECTED, A2ATaskState.REJECTED),
            (TaskStatus.AUTH_REQUIRED, A2ATaskState.AUTH_REQUIRED),
        ],
    )
    def test_mapping(
        self,
        internal: TaskStatus,
        expected: A2ATaskState,
    ) -> None:
        """Each internal status maps to the correct A2A state."""
        assert to_a2a(internal) == expected

    @pytest.mark.unit
    def test_all_statuses_covered(self) -> None:
        """Every TaskStatus member has a mapping."""
        for status in TaskStatus:
            result = to_a2a(status)
            assert isinstance(result, A2ATaskState)


class TestFromA2A:
    """A2ATaskState -> Internal TaskStatus mapping."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("a2a_state", "expected"),
        [
            (A2ATaskState.SUBMITTED, TaskStatus.CREATED),
            (A2ATaskState.WORKING, TaskStatus.IN_PROGRESS),
            (A2ATaskState.INPUT_REQUIRED, TaskStatus.BLOCKED),
            (A2ATaskState.COMPLETED, TaskStatus.COMPLETED),
            (A2ATaskState.FAILED, TaskStatus.FAILED),
            (A2ATaskState.CANCELED, TaskStatus.CANCELLED),
            (A2ATaskState.REJECTED, TaskStatus.REJECTED),
            (A2ATaskState.AUTH_REQUIRED, TaskStatus.AUTH_REQUIRED),
        ],
    )
    def test_mapping(
        self,
        a2a_state: A2ATaskState,
        expected: TaskStatus,
    ) -> None:
        """Each A2A state maps to the correct internal status."""
        assert from_a2a(a2a_state) == expected

    @pytest.mark.unit
    def test_all_a2a_states_covered(self) -> None:
        """Every A2ATaskState member has a reverse mapping."""
        for state in A2ATaskState:
            result = from_a2a(state)
            assert isinstance(result, TaskStatus)


class TestRoundTrip:
    """Round-trip mapping preserves essential state."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "a2a_state",
        list(A2ATaskState),
    )
    def test_a2a_round_trip(self, a2a_state: A2ATaskState) -> None:
        """from_a2a -> to_a2a returns the original A2A state."""
        internal = from_a2a(a2a_state)
        back = to_a2a(internal)
        assert back == a2a_state
