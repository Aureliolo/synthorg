"""Tests for the lifecycle-exit-reachability gate.

The gate exists because a state whose only route out needs data the entity may
not have is a state with no exit. The task lifecycle had one: ``FAILED`` could
only reach a terminal through ``ASSIGNED``, which needs an assignee, and a task
that failed before assignment has none. Its project then could not be deleted
by any route.

These cover the shape the gate must reject, the shape it must accept, the
plain-terminal-reachability trap it must NOT fall for, and the real machines.
"""

import pytest
from scripts.check_lifecycle_exit_reachable import _machine_violations, main

from synthorg.core.state_machine import StateMachine
from synthorg.core.task_enums import TaskStatus
from synthorg.observability.events.task import (
    TASK_TRANSITION_CONFIG_ERROR,
    TASK_TRANSITION_INVALID,
)

pytestmark = pytest.mark.unit


def _machine(
    transitions: dict[TaskStatus, frozenset[TaskStatus]],
    *,
    unconditional: frozenset[TaskStatus],
) -> StateMachine[TaskStatus]:
    """Build a machine over a partial table for the gate to walk.

    Returns:
        A machine with no ``all_states`` coverage check, so a fixture table
        can name only the states the case is about.
    """
    return StateMachine(
        transitions,
        name="fixture_status",
        invalid_event=TASK_TRANSITION_INVALID,
        config_event=TASK_TRANSITION_CONFIG_ERROR,
        unconditional_targets=unconditional,
    )


class TestDetection:
    def test_only_conditional_exit_is_a_violation(self) -> None:
        # The exact C6 shape: FAILED reaches a terminal, but only through
        # ASSIGNED, which needs an assignee the failed task never got.
        machine = _machine(
            {
                TaskStatus.FAILED: frozenset({TaskStatus.ASSIGNED}),
                TaskStatus.ASSIGNED: frozenset({TaskStatus.CANCELLED}),
                TaskStatus.CANCELLED: frozenset(),
            },
            unconditional=frozenset({TaskStatus.CANCELLED}),
        )
        violations = list(_machine_violations(machine))
        assert [v.state for v in violations] == [TaskStatus.FAILED.value]
        assert violations[0].allowed == (TaskStatus.ASSIGNED.value,)

    def test_terminal_reachability_alone_would_have_passed_it(self) -> None:
        # Guards the gate's own premise: the violating table above DOES have a
        # path to a terminal, so a plain reachability check accepts it. The
        # unconditional restriction is what makes the check worth having.
        machine = _machine(
            {
                TaskStatus.FAILED: frozenset({TaskStatus.ASSIGNED}),
                TaskStatus.ASSIGNED: frozenset({TaskStatus.CANCELLED}),
                TaskStatus.CANCELLED: frozenset(),
            },
            unconditional=frozenset({TaskStatus.CANCELLED}),
        )
        assert machine.path_to(TaskStatus.FAILED, TaskStatus.CANCELLED) is not None

    def test_direct_unconditional_exit_passes(self) -> None:
        machine = _machine(
            {
                TaskStatus.FAILED: frozenset(
                    {TaskStatus.ASSIGNED, TaskStatus.CANCELLED}
                ),
                TaskStatus.ASSIGNED: frozenset({TaskStatus.CANCELLED}),
                TaskStatus.CANCELLED: frozenset(),
            },
            unconditional=frozenset({TaskStatus.CANCELLED}),
        )
        assert list(_machine_violations(machine)) == []

    def test_a_terminal_state_exits_trivially(self) -> None:
        machine = _machine(
            {TaskStatus.CANCELLED: frozenset()},
            unconditional=frozenset({TaskStatus.CANCELLED}),
        )
        assert list(_machine_violations(machine)) == []

    def test_a_chain_of_unconditional_hops_counts(self) -> None:
        machine = _machine(
            {
                TaskStatus.BLOCKED: frozenset({TaskStatus.FAILED}),
                TaskStatus.FAILED: frozenset({TaskStatus.CANCELLED}),
                TaskStatus.CANCELLED: frozenset(),
            },
            unconditional=frozenset({TaskStatus.FAILED, TaskStatus.CANCELLED}),
        )
        assert list(_machine_violations(machine)) == []


class TestRealRepository:
    def test_shipped_machines_pass(self) -> None:
        assert main([]) == 0
