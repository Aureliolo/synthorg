"""Unit tests for :func:`busy_agent_ids`.

Pins the runtime-state definition of "busy": an agent is working while it holds
an ``IN_PROGRESS`` task OR a live agent-state row, never by any lifecycle flag.
Both the org overview and per-department utilisation build on this, so the rules
live here.

The second half is not decoration. Reading the task board alone reported an org
idle for the whole of a 54-minute decomposition, because a planning session runs
as a staffed agent and moves no task: the objective it is planning stays at
``CREATED`` until dispatch.
"""

import pytest

from synthorg.core.task import Task
from synthorg.core.task_activity import busy_agent_ids
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from tests._shared import as_uuid


def _task(label: str, *, status: TaskStatus, assigned_to: str) -> Task:
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description="busy_agent_ids fixture.",
        type=TaskType.DEVELOPMENT,
        project="project-1",
        priority=Priority.MEDIUM,
        status=status,
        assigned_to=assigned_to,
        created_by="engine",
    )


pytestmark = pytest.mark.unit


def test_returns_in_progress_assignees() -> None:
    tasks = (
        _task("t1", status=TaskStatus.IN_PROGRESS, assigned_to="alice"),
        _task("t2", status=TaskStatus.IN_PROGRESS, assigned_to="bob"),
    )
    assert busy_agent_ids(tasks) == {"alice", "bob"}


def test_excludes_non_in_progress_statuses() -> None:
    tasks = (
        _task("t1", status=TaskStatus.ASSIGNED, assigned_to="alice"),
        _task("t2", status=TaskStatus.IN_REVIEW, assigned_to="bob"),
        _task("t3", status=TaskStatus.IN_PROGRESS, assigned_to="carol"),
    )
    assert busy_agent_ids(tasks) == {"carol"}


def test_candidates_filter_restricts_result() -> None:
    tasks = (
        _task("t1", status=TaskStatus.IN_PROGRESS, assigned_to="alice"),
        _task("t2", status=TaskStatus.IN_PROGRESS, assigned_to="bob"),
    )
    assert busy_agent_ids(tasks, candidates={"alice"}) == {"alice"}


def test_duplicate_assignee_counts_once() -> None:
    tasks = (
        _task("t1", status=TaskStatus.IN_PROGRESS, assigned_to="alice"),
        _task("t2", status=TaskStatus.IN_PROGRESS, assigned_to="alice"),
    )
    assert busy_agent_ids(tasks) == {"alice"}


def test_empty_tasks_is_empty_set() -> None:
    assert busy_agent_ids(()) == set()
    assert busy_agent_ids((), candidates={"alice"}) == set()


class TestALiveRunCountsWithNoTaskAtAll:
    """The half the task board structurally cannot answer."""

    def test_a_running_agent_with_no_task_is_busy(self) -> None:
        assert busy_agent_ids((), running={"planner"}) == {"planner"}

    def test_a_running_agent_joins_the_board_rather_than_replacing_it(self) -> None:
        tasks = (_task("t1", status=TaskStatus.IN_PROGRESS, assigned_to="alice"),)
        assert busy_agent_ids(tasks, running={"planner"}) == {"alice", "planner"}

    def test_an_agent_in_both_counts_once(self) -> None:
        tasks = (_task("t1", status=TaskStatus.IN_PROGRESS, assigned_to="alice"),)
        assert busy_agent_ids(tasks, running={"alice"}) == {"alice"}

    def test_the_candidate_filter_applies_to_running_agents_too(self) -> None:
        # Otherwise a department's utilisation would count somebody else's
        # planner as one of its own.
        assert busy_agent_ids((), candidates={"alice"}, running={"planner"}) == set()

    def test_omitting_running_reads_the_board_alone(self) -> None:
        # The caller with no persistence still gets an answer, and it is the
        # board's, not a claim that nothing is running.
        tasks = (_task("t1", status=TaskStatus.IN_PROGRESS, assigned_to="alice"),)
        assert busy_agent_ids(tasks) == {"alice"}
