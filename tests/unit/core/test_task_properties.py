"""Property-based tests for Task model invariants and transitions."""

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from synthorg.core.enums import (
    Complexity,
    Priority,
    TaskStatus,
    TaskType,
)
from synthorg.core.task import Task
from synthorg.core.task_transitions import VALID_TRANSITIONS

pytestmark = pytest.mark.unit

_not_blank = st.text(min_size=1, max_size=30).filter(lambda s: s.strip())

_task_types = st.sampled_from(TaskType)
_priorities = st.sampled_from(Priority)
_complexities = st.sampled_from(Complexity)

_TASK_DEFAULTS: dict[str, Any] = {
    "id": "task-001",
    "title": "Test task",
    "description": "A test task",
    "type": TaskType.DEVELOPMENT,
    "priority": Priority.MEDIUM,
    "project": "proj-001",
    "created_by": "agent-creator",
    "status": TaskStatus.CREATED,
    "assigned_to": None,
    "dependencies": (),
}


def _make_task_kwargs(**overrides: Any) -> dict[str, Any]:
    return {**_TASK_DEFAULTS, **overrides}


_roundtrip_st = st.fixed_dictionaries(
    {
        "title": _not_blank,
        "description": _not_blank,
        "task_type": _task_types,
        "priority": _priorities,
        "complexity": _complexities,
        "budget": st.floats(min_value=0.0, max_value=1000.0, allow_nan=False),
    }
)


class TestTaskRoundtripProperties:
    @given(data=_roundtrip_st)
    @settings(max_examples=100)
    def test_model_dump_validate_roundtrip(self, data: dict[str, Any]) -> None:
        task = Task(
            id="task-rt-001",
            title=data["title"],
            description=data["description"],
            type=data["task_type"],
            priority=data["priority"],
            project="proj-rt",
            created_by="agent-rt",
            estimated_complexity=data["complexity"],
            budget_limit=data["budget"],
        )
        dumped = task.model_dump()
        restored = Task.model_validate(dumped)
        assert restored == task


class TestSelfDependencyProperties:
    @given(task_id=_not_blank)
    @settings(max_examples=100)
    def test_self_dependency_always_rejected(self, task_id: str) -> None:
        with pytest.raises(ValidationError, match="cannot depend on itself"):
            Task(
                **_make_task_kwargs(
                    id=task_id,
                    dependencies=(task_id,),
                ),
            )


class TestWithTransitionProperties:
    @given(
        target=st.sampled_from(
            list(VALID_TRANSITIONS[TaskStatus.CREATED]),
        ),
    )
    @settings(max_examples=20)
    def test_valid_transition_from_created(self, target: TaskStatus) -> None:
        task = Task(**_make_task_kwargs())
        # CREATED can only go to ASSIGNED, which requires assigned_to.
        # If VALID_TRANSITIONS[CREATED] gains more entries, extend the
        # kwargs mapping below.
        assigned_to = "agent-a" if target == TaskStatus.ASSIGNED else None
        new_task = task.with_transition(target, assigned_to=assigned_to)
        assert new_task.status == target

    @given(
        target=st.sampled_from(list(TaskStatus)).filter(
            lambda s: s not in VALID_TRANSITIONS[TaskStatus.CREATED],
        ),
    )
    @settings(max_examples=50)
    def test_invalid_transition_from_created_raises(
        self,
        target: TaskStatus,
    ) -> None:
        task = Task(**_make_task_kwargs())
        with pytest.raises(ValueError, match="Invalid task status transition"):
            task.with_transition(target)

    def test_valid_transition_chain(self) -> None:
        task = Task(**_make_task_kwargs())
        assigned = task.with_transition(
            TaskStatus.ASSIGNED,
            assigned_to="agent-a",
        )
        assert assigned.status == TaskStatus.ASSIGNED

        in_progress = assigned.with_transition(TaskStatus.IN_PROGRESS)
        assert in_progress.status == TaskStatus.IN_PROGRESS

        in_review = in_progress.with_transition(TaskStatus.IN_REVIEW)
        assert in_review.status == TaskStatus.IN_REVIEW

        completed = in_review.with_transition(TaskStatus.COMPLETED)
        assert completed.status == TaskStatus.COMPLETED

    def test_with_transition_rejects_status_override(self) -> None:
        task = Task(**_make_task_kwargs())
        with pytest.raises(ValueError, match="status override is not allowed"):
            task.with_transition(
                TaskStatus.ASSIGNED,
                status=TaskStatus.IN_PROGRESS,
                assigned_to="agent-a",
            )
