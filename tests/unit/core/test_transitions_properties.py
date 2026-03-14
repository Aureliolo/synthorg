"""Property-based tests for task transition validation invariants."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthorg.core.enums import TaskStatus
from synthorg.core.task_transitions import VALID_TRANSITIONS, validate_transition

pytestmark = pytest.mark.unit

_TERMINAL_STATES = frozenset(s for s in TaskStatus if len(VALID_TRANSITIONS[s]) == 0)
_all_statuses = st.sampled_from(TaskStatus)

_TRANSITION_ERR = r"Invalid task status transition|has no entry"


class TestValidateTransitionProperties:
    @given(current=_all_statuses, target=_all_statuses)
    @settings(max_examples=200)
    def test_matches_valid_transitions_map(
        self,
        current: TaskStatus,
        target: TaskStatus,
    ) -> None:
        allowed = VALID_TRANSITIONS[current]
        if target in allowed:
            validate_transition(current, target)
        else:
            with pytest.raises(
                ValueError,
                match=_TRANSITION_ERR,
            ):
                validate_transition(current, target)

    @given(
        status=st.sampled_from(list(_TERMINAL_STATES)),
        target=_all_statuses,
    )
    @settings(max_examples=100)
    def test_terminal_states_have_no_outgoing(
        self,
        status: TaskStatus,
        target: TaskStatus,
    ) -> None:
        assert len(VALID_TRANSITIONS[status]) == 0
        with pytest.raises(
            ValueError,
            match=_TRANSITION_ERR,
        ):
            validate_transition(status, target)

    @given(current=_all_statuses)
    @settings(max_examples=50)
    def test_every_status_has_transition_entry(
        self,
        current: TaskStatus,
    ) -> None:
        assert current in VALID_TRANSITIONS

    @given(
        current=_all_statuses.filter(
            lambda s: len(VALID_TRANSITIONS[s]) > 0,
        ),
    )
    @settings(max_examples=50)
    def test_all_valid_transitions_succeed(
        self,
        current: TaskStatus,
    ) -> None:
        for target in VALID_TRANSITIONS[current]:
            validate_transition(current, target)

    @given(current=_all_statuses)
    @settings(max_examples=50)
    def test_invalid_transitions_raise(
        self,
        current: TaskStatus,
    ) -> None:
        allowed = VALID_TRANSITIONS[current]
        invalid = set(TaskStatus) - set(allowed)
        for target in invalid:
            with pytest.raises(
                ValueError,
                match=_TRANSITION_ERR,
            ):
                validate_transition(current, target)
