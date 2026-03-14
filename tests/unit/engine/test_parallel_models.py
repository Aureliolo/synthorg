"""Tests for parallel execution models."""

from datetime import date

import pytest
from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.enums import (
    Complexity,
    Priority,
    SeniorityLevel,
    TaskStatus,
    TaskType,
)
from synthorg.core.task import Task
from synthorg.engine.context import DEFAULT_MAX_TURNS, AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.engine.parallel_models import (
    AgentAssignment,
    AgentOutcome,
    ParallelExecutionGroup,
    ParallelExecutionResult,
    ParallelProgress,
)
from synthorg.engine.prompt import SystemPrompt
from synthorg.engine.run_result import AgentRunResult
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage


def _make_identity(
    name: str = "test-agent",
    **kwargs: object,
) -> AgentIdentity:
    defaults: dict[str, object] = {
        "role": "engineer",
        "department": "engineering",
        "level": SeniorityLevel.MID,
        "hiring_date": date(2026, 1, 15),
        "personality": PersonalityConfig(traits=("analytical",)),
        "model": ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
    }
    defaults.update(kwargs)
    return AgentIdentity(name=name, **defaults)  # type: ignore[arg-type]


def _make_task(
    title: str = "test-task",
    **kwargs: object,
) -> Task:
    defaults: dict[str, object] = {
        "id": f"task-{title}",
        "description": "A test task",
        "type": TaskType.DEVELOPMENT,
        "priority": Priority.MEDIUM,
        "project": "test-project",
        "created_by": "tester",
        "assigned_to": "test-agent",
        "status": TaskStatus.ASSIGNED,
        "estimated_complexity": Complexity.SIMPLE,
    }
    defaults.update(kwargs)
    return Task(title=title, **defaults)  # type: ignore[arg-type]


def _make_assignment(
    name: str = "agent",
    title: str = "task",
    /,
    **kwargs: object,
) -> AgentAssignment:
    return AgentAssignment(
        identity=_make_identity(name),
        task=_make_task(title),
        **kwargs,  # type: ignore[arg-type]
    )


def _make_run_result(
    identity: AgentIdentity | None = None,
    task: Task | None = None,
    reason: TerminationReason = TerminationReason.COMPLETED,
) -> AgentRunResult:
    identity = identity or _make_identity()
    task = task or _make_task()
    ctx = AgentContext.from_identity(identity, task=task)
    error_msg = "test error" if reason == TerminationReason.ERROR else None
    execution_result = ExecutionResult(
        context=ctx,
        termination_reason=reason,
        error_message=error_msg,
    )
    return AgentRunResult(
        execution_result=execution_result,
        system_prompt=SystemPrompt(
            content="test",
            template_version="1.0",
            estimated_tokens=1,
            sections=("identity",),
            metadata={"agent_id": str(identity.id)},
        ),
        duration_seconds=1.0,
        agent_id=str(identity.id),
        task_id=task.id,
    )


@pytest.mark.unit
class TestAgentAssignment:
    """AgentAssignment frozen model."""

    def test_minimal_construction(self) -> None:
        identity = _make_identity()
        task = _make_task()
        assignment = AgentAssignment(identity=identity, task=task)

        assert assignment.identity == identity
        assert assignment.task == task
        assert assignment.completion_config is None
        assert assignment.max_turns == DEFAULT_MAX_TURNS
        assert assignment.timeout_seconds is None
        assert assignment.memory_messages == ()
        assert assignment.resource_claims == ()

    def test_full_construction(self) -> None:
        identity = _make_identity()
        task = _make_task()
        msg = ChatMessage(role=MessageRole.USER, content="hi")
        assignment = AgentAssignment(
            identity=identity,
            task=task,
            max_turns=5,
            timeout_seconds=60.0,
            memory_messages=(msg,),
            resource_claims=("src/main.py", "README.md"),
        )

        assert assignment.max_turns == 5
        assert assignment.timeout_seconds == 60.0
        assert len(assignment.memory_messages) == 1
        assert assignment.resource_claims == ("src/main.py", "README.md")

    def test_frozen(self) -> None:
        assignment = _make_assignment()
        with pytest.raises(ValidationError):
            assignment.max_turns = 10  # type: ignore[misc]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_turns", 0),
            ("max_turns", -1),
            ("timeout_seconds", 0),
            ("timeout_seconds", -5.0),
        ],
    )
    def test_invalid_numeric_constraints(
        self,
        field: str,
        value: object,
    ) -> None:
        with pytest.raises(ValidationError):
            _make_assignment(**{field: value})

    def test_duplicate_resource_claims_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match=r"[Dd]uplicate.*resource",
        ):
            _make_assignment(
                resource_claims=("src/a.py", "src/a.py"),
            )

    def test_agent_id_property(self) -> None:
        assignment = _make_assignment()
        assert assignment.agent_id == str(assignment.identity.id)

    def test_task_id_property(self) -> None:
        assignment = _make_assignment()
        assert assignment.task_id == assignment.task.id


@pytest.mark.unit
class TestParallelExecutionGroup:
    """ParallelExecutionGroup frozen model with validators."""

    def test_minimal_construction(self) -> None:
        a = _make_assignment("a1", "t1")
        group = ParallelExecutionGroup(
            group_id="grp-1",
            assignments=(a,),
        )

        assert group.group_id == "grp-1"
        assert len(group.assignments) == 1
        assert group.max_concurrency is None
        assert group.fail_fast is False

    def test_full_construction(self) -> None:
        a1 = _make_assignment("a1", "t1")
        a2 = _make_assignment("a2", "t2")
        group = ParallelExecutionGroup(
            group_id="grp-2",
            assignments=(a1, a2),
            max_concurrency=2,
            fail_fast=True,
        )

        assert len(group.assignments) == 2
        assert group.max_concurrency == 2
        assert group.fail_fast is True

    def test_frozen(self) -> None:
        group = ParallelExecutionGroup(
            group_id="grp-1",
            assignments=(_make_assignment(),),
        )
        with pytest.raises(ValidationError):
            group.fail_fast = True  # type: ignore[misc]

    def test_empty_assignments_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one"):
            ParallelExecutionGroup(
                group_id="grp",
                assignments=(),
            )

    def test_duplicate_task_ids_rejected(self) -> None:
        identity1 = _make_identity("a1")
        identity2 = _make_identity("a2")
        task = _make_task("shared")
        a1 = AgentAssignment(identity=identity1, task=task)
        a2 = AgentAssignment(identity=identity2, task=task)

        with pytest.raises(ValidationError, match=r"[Dd]uplicate.*task"):
            ParallelExecutionGroup(
                group_id="grp",
                assignments=(a1, a2),
            )

    def test_duplicate_agent_ids_rejected(self) -> None:
        identity = _make_identity("same")
        t1 = _make_task("t1")
        t2 = _make_task("t2")
        a1 = AgentAssignment(identity=identity, task=t1)
        a2 = AgentAssignment(identity=identity, task=t2)

        with pytest.raises(ValidationError, match=r"[Dd]uplicate.*agent"):
            ParallelExecutionGroup(
                group_id="grp",
                assignments=(a1, a2),
            )

    def test_max_concurrency_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ParallelExecutionGroup(
                group_id="grp",
                assignments=(_make_assignment(),),
                max_concurrency=0,
            )

    def test_max_concurrency_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ParallelExecutionGroup(
                group_id="grp",
                assignments=(_make_assignment(),),
                max_concurrency=-1,
            )

    def test_blank_group_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ParallelExecutionGroup(
                group_id="  ",
                assignments=(_make_assignment(),),
            )


@pytest.mark.unit
class TestAgentOutcome:
    """AgentOutcome frozen model."""

    def test_success_outcome(self) -> None:
        identity = _make_identity()
        task = _make_task()
        result = _make_run_result(identity, task)
        outcome = AgentOutcome(
            task_id=task.id,
            agent_id=str(identity.id),
            result=result,
        )

        assert outcome.result is result
        assert outcome.error is None
        assert outcome.is_success is True

    def test_error_outcome(self) -> None:
        outcome = AgentOutcome(
            task_id="t1",
            agent_id="a1",
            error="something failed",
        )

        assert outcome.result is None
        assert outcome.error == "something failed"
        assert outcome.is_success is False

    def test_failed_run_result(self) -> None:
        identity = _make_identity()
        task = _make_task()
        result = _make_run_result(identity, task, reason=TerminationReason.ERROR)
        outcome = AgentOutcome(
            task_id=task.id,
            agent_id=str(identity.id),
            result=result,
        )

        assert outcome.is_success is False

    def test_mismatched_task_id_rejected(self) -> None:
        identity = _make_identity()
        task = _make_task()
        result = _make_run_result(identity, task)
        with pytest.raises(
            ValidationError,
            match=r"result\.task_id.*must match task_id",
        ):
            AgentOutcome(
                task_id="wrong-task-id",
                agent_id=str(identity.id),
                result=result,
            )

    def test_mismatched_agent_id_rejected(self) -> None:
        identity = _make_identity()
        task = _make_task()
        result = _make_run_result(identity, task)
        with pytest.raises(
            ValidationError,
            match=r"result\.agent_id.*must match agent_id",
        ):
            AgentOutcome(
                task_id=task.id,
                agent_id="wrong-agent-id",
                result=result,
            )

    def test_both_none_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="Exactly one of result or error",
        ):
            AgentOutcome(task_id="t1", agent_id="a1")

    def test_both_set_rejected(self) -> None:
        identity = _make_identity()
        task = _make_task()
        result = _make_run_result(identity, task)
        with pytest.raises(
            ValidationError,
            match="Exactly one of result or error",
        ):
            AgentOutcome(
                task_id=task.id,
                agent_id=str(identity.id),
                result=result,
                error="also has error",
            )

    def test_frozen(self) -> None:
        outcome = AgentOutcome(
            task_id="t1",
            agent_id="a1",
            error="x",
        )
        with pytest.raises(ValidationError):
            outcome.error = "y"  # type: ignore[misc]


@pytest.mark.unit
class TestParallelExecutionResult:
    """ParallelExecutionResult frozen model with computed fields."""

    def test_all_succeeded(self) -> None:
        i1, t1 = _make_identity("a1"), _make_task("t1")
        i2, t2 = _make_identity("a2"), _make_task("t2")
        r1 = _make_run_result(i1, t1)
        r2 = _make_run_result(i2, t2)
        o1 = AgentOutcome(
            task_id=t1.id,
            agent_id=str(i1.id),
            result=r1,
        )
        o2 = AgentOutcome(
            task_id=t2.id,
            agent_id=str(i2.id),
            result=r2,
        )
        result = ParallelExecutionResult(
            group_id="grp",
            outcomes=(o1, o2),
            total_duration_seconds=5.0,
        )

        assert result.agents_succeeded == 2
        assert result.agents_failed == 0
        assert result.all_succeeded is True

    def test_partial_failure(self) -> None:
        i1, t1 = _make_identity("a1"), _make_task("t1")
        r1 = _make_run_result(i1, t1)
        o1 = AgentOutcome(
            task_id=t1.id,
            agent_id=str(i1.id),
            result=r1,
        )
        o2 = AgentOutcome(
            task_id="t2",
            agent_id="a2",
            error="boom",
        )
        result = ParallelExecutionResult(
            group_id="grp",
            outcomes=(o1, o2),
            total_duration_seconds=3.0,
        )

        assert result.agents_succeeded == 1
        assert result.agents_failed == 1
        assert result.all_succeeded is False

    def test_total_cost_usd(self) -> None:
        i1, t1 = _make_identity("a1"), _make_task("t1")
        r1 = _make_run_result(i1, t1)
        o1 = AgentOutcome(
            task_id=t1.id,
            agent_id=str(i1.id),
            result=r1,
        )
        o2 = AgentOutcome(
            task_id="t2",
            agent_id="a2",
            error="boom",
        )
        result = ParallelExecutionResult(
            group_id="grp",
            outcomes=(o1, o2),
            total_duration_seconds=3.0,
        )

        # Error outcome contributes no cost, success contributes its cost
        assert o1.result is not None
        assert result.total_cost_usd == o1.result.total_cost_usd

    def test_frozen(self) -> None:
        result = ParallelExecutionResult(
            group_id="grp",
            outcomes=(),
            total_duration_seconds=0.0,
        )
        with pytest.raises(ValidationError):
            result.group_id = "x"  # type: ignore[misc]


@pytest.mark.unit
class TestParallelProgress:
    """ParallelProgress frozen snapshot model."""

    def test_construction(self) -> None:
        progress = ParallelProgress(
            group_id="grp",
            total=4,
            completed=2,
            in_progress=1,
            succeeded=2,
            failed=0,
        )

        assert progress.total == 4
        assert progress.completed == 2
        assert progress.in_progress == 1
        assert progress.pending == 1  # computed: 4 - 2 - 1
        assert progress.succeeded == 2
        assert progress.failed == 0

    def test_pending_computed(self) -> None:
        progress = ParallelProgress(
            group_id="grp",
            total=5,
            completed=1,
            in_progress=2,
            succeeded=1,
            failed=0,
        )
        assert progress.pending == 2  # 5 - 1 - 2

    def test_completed_plus_in_progress_exceeds_total_rejected(
        self,
    ) -> None:
        with pytest.raises(
            ValidationError,
            match=r"completed.*in_progress.*must not exceed total",
        ):
            ParallelProgress(
                group_id="grp",
                total=1,
                completed=1,
                in_progress=1,
                succeeded=1,
                failed=0,
            )

    def test_succeeded_plus_failed_exceeds_completed_rejected(
        self,
    ) -> None:
        with pytest.raises(
            ValidationError,
            match=r"succeeded.*failed.*must not exceed completed",
        ):
            ParallelProgress(
                group_id="grp",
                total=5,
                completed=1,
                in_progress=0,
                succeeded=1,
                failed=1,
            )

    @pytest.mark.parametrize(
        "field",
        ["total", "completed", "in_progress", "succeeded", "failed"],
    )
    def test_negative_values_rejected(self, field: str) -> None:
        kwargs: dict[str, object] = {
            "group_id": "grp",
            "total": 1,
            "completed": 0,
            "in_progress": 0,
            "succeeded": 0,
            "failed": 0,
        }
        kwargs[field] = -1
        with pytest.raises(ValidationError):
            ParallelProgress(**kwargs)  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        progress = ParallelProgress(
            group_id="grp",
            total=1,
            completed=0,
            in_progress=0,
            succeeded=0,
            failed=0,
        )
        with pytest.raises(ValidationError):
            progress.total = 10  # type: ignore[misc]
