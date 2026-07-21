"""Unit tests for :class:`InProcessSubAgentRunner`."""

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
from synthorg.engine.delegation.errors import DelegationTargetNotFoundError
from synthorg.engine.delegation.models import DelegationSpec
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

_CHILD_AGENT_ID = as_uuid("child-agent")
_CHILD_TASK_ID = as_uuid("child-task")


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
    return cast(AgentEngine, mock_of[AgentEngine](run=AsyncMock(return_value=result)))


def _spec(target: str = str(_CHILD_AGENT_ID)) -> DelegationSpec:
    """Build a delegation spec addressed to ``target``."""
    return DelegationSpec(
        target=target,
        title="Investigate flakiness",
        description="Find the root cause of the intermittent failure.",
        project="proj-001",
        parent_task_id=str(as_uuid("parent-task")),
        requested_by=str(as_uuid("parent-agent")),
    )


def _task_engine() -> TaskEngine:
    """Build a fake task engine returning the persisted child task."""
    task = _child_task()
    return cast(
        TaskEngine,
        mock_of[TaskEngine](
            create_task=AsyncMock(return_value=task),
            transition_task=AsyncMock(return_value=(task, TaskStatus.CREATED)),
        ),
    )


@pytest.mark.unit
class TestInProcessSubAgentRunner:
    def test_satisfies_protocol(self) -> None:
        runner = InProcessSubAgentRunner(
            engine=_engine(_run_result(_child_identity())),
            task_engine=_task_engine(),
            agent_registry=mock_of[AgentRegistryProtocol](
                get=AsyncMock(return_value=None),
                get_by_name=AsyncMock(return_value=None),
            ),
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
        registry = mock_of[AgentRegistryProtocol](
            get=AsyncMock(return_value=identity),
            get_by_name=AsyncMock(return_value=None),
        )
        runner = InProcessSubAgentRunner(
            engine=engine,
            task_engine=_task_engine(),
            agent_registry=registry,
        )

        result = await runner.run(_spec(), max_turns=7)

        run_call = cast(AsyncMock, engine.run).await_args
        assert run_call is not None
        assert run_call.kwargs["max_turns"] == 7
        assert result.child_task_id == str(_CHILD_TASK_ID)
        assert result.target_agent_id == str(_CHILD_AGENT_ID)
        assert result.is_success is True
        assert result.termination_reason == TerminationReason.COMPLETED
        assert result.final_answer == "Root cause: a race in the retry loop."
        assert "Root cause" in result.transcript_summary
        assert "Do the thing" in result.transcript_summary

    async def test_resolves_target_by_name_when_id_misses(self) -> None:
        identity = _child_identity(name="Researcher")
        registry = mock_of[AgentRegistryProtocol](
            get=AsyncMock(return_value=None),
            get_by_name=AsyncMock(return_value=identity),
        )
        runner = InProcessSubAgentRunner(
            engine=_engine(_run_result(identity)),
            task_engine=_task_engine(),
            agent_registry=registry,
        )

        result = await runner.run(_spec(target="Researcher"), max_turns=5)

        registry.get.assert_awaited_once()
        registry.get_by_name.assert_awaited_once()
        assert result.target_agent_id == str(_CHILD_AGENT_ID)

    async def test_unknown_target_raises(self) -> None:
        registry = mock_of[AgentRegistryProtocol](
            get=AsyncMock(return_value=None),
            get_by_name=AsyncMock(return_value=None),
        )
        runner = InProcessSubAgentRunner(
            engine=_engine(_run_result(_child_identity())),
            task_engine=_task_engine(),
            agent_registry=registry,
        )

        with pytest.raises(DelegationTargetNotFoundError):
            await runner.run(_spec(target="ghost"), max_turns=5)

    async def test_creates_and_assigns_child_task(self) -> None:
        identity = _child_identity()
        engine = _engine(_run_result(identity))
        task_engine = _task_engine()
        runner = InProcessSubAgentRunner(
            engine=engine,
            task_engine=task_engine,
            agent_registry=mock_of[AgentRegistryProtocol](
                get=AsyncMock(return_value=identity),
                get_by_name=AsyncMock(return_value=None),
            ),
        )

        await runner.run(_spec(), max_turns=5)

        cast(AsyncMock, task_engine.create_task).assert_awaited_once()
        transition_call = cast(AsyncMock, task_engine.transition_task).await_args
        assert transition_call is not None
        assert transition_call.kwargs["assigned_to"] == str(_CHILD_AGENT_ID)
        assert transition_call.kwargs["parent_task_id"] == str(as_uuid("parent-task"))
        # The child the engine runs is the assigned task, not the pre-transition
        # row, so stakes routing / assignment invariants see ASSIGNED state.
        run_call = cast(AsyncMock, engine.run).await_args
        assert run_call is not None
        assert run_call.kwargs["task"].status == TaskStatus.ASSIGNED

    async def test_failed_child_surfaces_non_success(self) -> None:
        identity = _child_identity()
        runner = InProcessSubAgentRunner(
            engine=_engine(
                _run_result(identity, termination_reason=TerminationReason.MAX_TURNS),
            ),
            task_engine=_task_engine(),
            agent_registry=mock_of[AgentRegistryProtocol](
                get=AsyncMock(return_value=identity),
                get_by_name=AsyncMock(return_value=None),
            ),
        )

        result = await runner.run(_spec(), max_turns=5)

        assert result.is_success is False
        assert result.termination_reason == TerminationReason.MAX_TURNS

    async def test_reports_child_cost(self) -> None:
        identity = _child_identity()
        runner = InProcessSubAgentRunner(
            engine=_engine(_run_result(identity, cost=0.42)),
            task_engine=_task_engine(),
            agent_registry=mock_of[AgentRegistryProtocol](
                get=AsyncMock(return_value=identity),
                get_by_name=AsyncMock(return_value=None),
            ),
        )

        result = await runner.run(_spec(), max_turns=5)

        assert result.total_cost == pytest.approx(0.42)
        assert result.currency == DEFAULT_CURRENCY
