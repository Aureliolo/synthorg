"""Unit tests for :class:`InProcessSubAgentRunner`."""

import asyncio
from datetime import date
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    ToolPermissions,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.delegation.errors import (
    SubAgentDelegationDepthExceededError,
    SubAgentDelegationTargetNotFoundError,
)
from synthorg.engine.delegation.models import SubAgentDelegationSpec
from synthorg.engine.delegation.protocol import SubAgentRunner
from synthorg.engine.delegation.runner import InProcessSubAgentRunner
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.prompt import SystemPrompt
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, TokenUsage
from tests._shared import mock_of
from tests._shared.ids import as_uuid

pytestmark = pytest.mark.unit

_CHILD_AGENT_ID = as_uuid("child-agent")
_CHILD_TASK_ID = as_uuid("child-task")
_PARENT_TASK_ID = as_uuid("parent-task")
_PARENT_AGENT_ID = as_uuid("parent-agent")


def _child_identity(name: str = "Child Agent") -> AgentIdentity:
    """Build a child agent identity for delegation tests."""
    return AgentIdentity(
        id=_CHILD_AGENT_ID,
        name=name,
        role="Researcher",
        department="Engineering",
        hiring_date=date(2026, 1, 1),
        personality=PersonalityConfig(traits=("analytical",)),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        tools=ToolPermissions(),
    )


def _child_task() -> Task:
    """Build the persisted child task the fake task engine returns."""
    return Task(
        id=_CHILD_TASK_ID,
        title="Investigate flakiness",
        description="Find the root cause.",
        type=TaskType.RESEARCH,
        priority=Priority.MEDIUM,
        project="proj-001",
        created_by="parent-agent",
        assigned_to=str(_CHILD_AGENT_ID),
        status=TaskStatus.ASSIGNED,
    )


def _run_result(
    identity: AgentIdentity,
    *,
    termination_reason: TerminationReason = TerminationReason.COMPLETED,
    cost: float = 0.0,
    conversation: tuple[ChatMessage, ...] = (),
) -> AgentRunResult:
    """Build an ``AgentRunResult`` a fake engine can return."""
    ctx = AgentContext.from_identity(identity, task=_child_task())
    for message in conversation:
        ctx = ctx.with_message(message)
    if cost:
        ctx = ctx.model_copy(
            update={
                "accumulated_cost": TokenUsage(
                    input_tokens=0,
                    output_tokens=0,
                    cost=cost,
                ),
            },
        )
    return AgentRunResult(
        execution_result=ExecutionResult(
            context=ctx,
            termination_reason=termination_reason,
            turns=(),
            error_message=(
                "child run failed"
                if termination_reason is TerminationReason.ERROR
                else None
            ),
        ),
        system_prompt=SystemPrompt(
            content="child prompt",
            template_version="test",
            estimated_tokens=1,
            sections=("identity",),
            metadata={"agent_id": str(identity.id)},
        ),
        duration_seconds=0.5,
        agent_id=str(identity.id),
        task_id=str(_CHILD_TASK_ID),
        currency=DEFAULT_CURRENCY,
    )


def _engine(result: AgentRunResult) -> AgentEngine:
    """Build an ``AgentEngine`` double whose ``run`` returns ``result``."""
    return cast(
        AgentEngine,
        mock_of[AgentEngine](run=AsyncMock(spec=AgentEngine.run, return_value=result)),
    )


def _registry(
    identity: AgentIdentity | None,
    *,
    by_name: bool = False,
) -> AgentRegistryProtocol:
    """Build an agent-registry double resolving to ``identity``."""
    return cast(
        AgentRegistryProtocol,
        mock_of[AgentRegistryProtocol](
            get=AsyncMock(
                spec=AgentRegistryProtocol.get,
                return_value=None if by_name else identity,
            ),
            get_by_name=AsyncMock(
                spec=AgentRegistryProtocol.get_by_name,
                return_value=identity if by_name else None,
            ),
        ),
    )


def _spec(target: str = str(_CHILD_AGENT_ID)) -> SubAgentDelegationSpec:
    """Build a delegation spec addressed to ``target``."""
    return SubAgentDelegationSpec(
        target=target,
        title="Investigate flakiness",
        description="Find the root cause of the intermittent failure.",
        project="proj-001",
        parent_task_id=str(_PARENT_TASK_ID),
        requested_by=str(_PARENT_AGENT_ID),
    )


def _task_engine(*, ancestors: dict[str, Task] | None = None) -> TaskEngine:
    """Build a fake task engine.

    ``ancestors`` maps a task id to the ``Task`` ``get_task`` returns for it,
    driving the parent-chain depth/cycle walk. When omitted, ``get_task``
    returns ``None`` (an empty chain, depth 0).
    """
    child = _child_task()
    lookup = ancestors or {}
    return cast(
        TaskEngine,
        mock_of[TaskEngine](
            create_task=AsyncMock(spec=TaskEngine.create_task, return_value=child),
            transition_task=AsyncMock(
                spec=TaskEngine.transition_task,
                return_value=(child, TaskStatus.CREATED),
            ),
            cancel_task=AsyncMock(
                spec=TaskEngine.cancel_task,
                return_value=(child, TaskStatus.ASSIGNED),
            ),
            get_task=AsyncMock(
                spec=TaskEngine.get_task,
                side_effect=lookup.get,
            ),
        ),
    )


def _ancestor(
    task_id: str,
    *,
    assigned_to: str,
    parent_task_id: str | None,
) -> Task:
    """Build an ancestor task for the parent-chain walk."""
    return Task(
        id=as_uuid(task_id),
        title="Ancestor",
        description="An ancestor task.",
        type=TaskType.RESEARCH,
        priority=Priority.MEDIUM,
        project="proj-001",
        created_by="parent-agent",
        assigned_to=assigned_to,
        parent_task_id=parent_task_id,
        status=TaskStatus.IN_PROGRESS,
    )


def _runner(
    engine: AgentEngine,
    task_engine: TaskEngine,
    registry: AgentRegistryProtocol,
) -> InProcessSubAgentRunner:
    return InProcessSubAgentRunner(
        engine=engine,
        task_engine=task_engine,
        agent_registry=registry,
    )


class TestInProcessSubAgentRunner:
    def test_satisfies_protocol(self) -> None:
        runner = _runner(
            _engine(_run_result(_child_identity())),
            _task_engine(),
            _registry(None),
        )
        assert isinstance(runner, SubAgentRunner)

    async def test_runs_child_and_returns_result(self) -> None:
        identity = _child_identity()
        engine = _engine(
            _run_result(
                identity,
                conversation=(
                    ChatMessage(role=MessageRole.USER, content="Do the thing"),
                    ChatMessage(
                        role=MessageRole.ASSISTANT,
                        content="Root cause: a race in the retry loop.",
                    ),
                ),
            ),
        )
        runner = _runner(engine, _task_engine(), _registry(identity))

        result = await runner.run(_spec(), max_turns=7, timeout_seconds=42.0)

        run_call = cast(AsyncMock, engine.run).await_args
        assert run_call is not None
        assert run_call.kwargs["max_turns"] == 7
        assert run_call.kwargs["timeout_seconds"] == 42.0
        assert result.child_task_id == str(_CHILD_TASK_ID)
        assert result.target_agent_id == str(_CHILD_AGENT_ID)
        assert result.is_success is True
        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.final_answer == "Root cause: a race in the retry loop."
        assert "Root cause" in result.transcript_summary
        assert "Do the thing" in result.transcript_summary

    async def test_resolves_target_by_name_when_id_misses(self) -> None:
        identity = _child_identity(name="Researcher")
        registry = _registry(identity, by_name=True)
        runner = _runner(_engine(_run_result(identity)), _task_engine(), registry)

        result = await runner.run(_spec(target="Researcher"), max_turns=5)

        cast(AsyncMock, registry.get).assert_awaited_once()
        cast(AsyncMock, registry.get_by_name).assert_awaited_once()
        assert result.target_agent_id == str(_CHILD_AGENT_ID)

    async def test_unknown_target_raises(self) -> None:
        runner = _runner(
            _engine(_run_result(_child_identity())),
            _task_engine(),
            _registry(None),
        )
        with pytest.raises(SubAgentDelegationTargetNotFoundError):
            await runner.run(_spec(target="ghost"), max_turns=5)

    async def test_creates_and_assigns_child_task(self) -> None:
        identity = _child_identity()
        engine = _engine(_run_result(identity))
        task_engine = _task_engine()
        runner = _runner(engine, task_engine, _registry(identity))

        await runner.run(_spec(), max_turns=5)

        cast(AsyncMock, task_engine.create_task).assert_awaited_once()
        transition_call = cast(AsyncMock, task_engine.transition_task).await_args
        assert transition_call is not None
        assert transition_call.kwargs["assigned_to"] == str(_CHILD_AGENT_ID)
        assert transition_call.kwargs["parent_task_id"] == str(_PARENT_TASK_ID)
        run_call = cast(AsyncMock, engine.run).await_args
        assert run_call is not None
        assert run_call.kwargs["task"].status == TaskStatus.ASSIGNED

    async def test_failed_child_surfaces_non_success(self) -> None:
        identity = _child_identity()
        runner = _runner(
            _engine(
                _run_result(identity, termination_reason=TerminationReason.MAX_TURNS),
            ),
            _task_engine(),
            _registry(identity),
        )

        result = await runner.run(_spec(), max_turns=5)

        assert result.is_success is False
        assert result.termination_reason == TerminationReason.MAX_TURNS

    @pytest.mark.parametrize(
        "termination_reason",
        [TerminationReason.ERROR, TerminationReason.CANCELLED],
    )
    async def test_non_completed_child_is_reported_not_raised(
        self, termination_reason: TerminationReason
    ) -> None:
        """An ERROR / CANCELLED child folds into a non-success result.

        Only a mid-flight ``asyncio.CancelledError`` propagates; a child that
        *terminates* ERROR or CANCELLED is a normal outcome the supervisor
        consumes, so the reason must pass through untouched with
        ``is_success`` false rather than raising.
        """
        identity = _child_identity()
        runner = _runner(
            _engine(_run_result(identity, termination_reason=termination_reason)),
            _task_engine(),
            _registry(identity),
        )

        result = await runner.run(_spec(), max_turns=5)

        assert result.is_success is False
        assert result.termination_reason is termination_reason

    async def test_reports_child_cost(self) -> None:
        identity = _child_identity()
        runner = _runner(
            _engine(_run_result(identity, cost=0.42)),
            _task_engine(),
            _registry(identity),
        )

        result = await runner.run(_spec(), max_turns=5)

        assert result.total_cost == pytest.approx(0.42)
        assert result.currency == DEFAULT_CURRENCY

    async def test_self_delegation_is_rejected_as_cycle(self) -> None:
        # The immediate parent task is assigned to the target agent, so the
        # target already appears as an ancestor assignee -> cycle.
        identity = _child_identity()
        parent = _ancestor(
            "parent-task",
            assigned_to=str(_CHILD_AGENT_ID),
            parent_task_id=None,
        )
        runner = _runner(
            _engine(_run_result(identity)),
            _task_engine(ancestors={str(_PARENT_TASK_ID): parent}),
            _registry(identity),
        )

        with pytest.raises(SubAgentDelegationDepthExceededError):
            await runner.run(_spec(), max_turns=5, max_depth=5)

    async def test_depth_limit_rejected(self) -> None:
        # A two-deep parent chain against max_depth=2 -> refused.
        identity = _child_identity()
        grandparent = _ancestor(
            "grandparent-task",
            assigned_to=str(as_uuid("other-agent")),
            parent_task_id=None,
        )
        parent = _ancestor(
            "parent-task",
            assigned_to=str(as_uuid("another-agent")),
            parent_task_id=str(as_uuid("grandparent-task")),
        )
        ancestors = {
            str(_PARENT_TASK_ID): parent,
            str(as_uuid("grandparent-task")): grandparent,
        }
        runner = _runner(
            _engine(_run_result(identity)),
            _task_engine(ancestors=ancestors),
            _registry(identity),
        )

        with pytest.raises(SubAgentDelegationDepthExceededError):
            await runner.run(_spec(), max_turns=5, max_depth=2)

    async def test_cancelled_child_run_marks_task_terminal(self) -> None:
        identity = _child_identity()
        engine = cast(
            AgentEngine,
            mock_of[AgentEngine](
                run=AsyncMock(
                    spec=AgentEngine.run,
                    side_effect=asyncio.CancelledError,
                ),
            ),
        )
        task_engine = _task_engine()
        runner = _runner(engine, task_engine, _registry(identity))

        with pytest.raises(asyncio.CancelledError):
            await runner.run(_spec(), max_turns=5)

        # The orphaned child task is cancelled (best-effort) before re-raise.
        cast(AsyncMock, task_engine.cancel_task).assert_awaited_once()
