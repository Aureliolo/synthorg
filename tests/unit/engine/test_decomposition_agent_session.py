"""Tests for the owner-run agent-session decomposition strategy."""

from typing import override
from uuid import UUID

import pytest
from pydantic import JsonValue

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.engine.decomposition.agent_session import (
    AgentSessionDecompositionConfig,
    AgentSessionDecompositionStrategy,
    SubmitDecompositionPlanTool,
    _PlanCapture,
)
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.tools.base import ToolExecutionResult
from tests._shared import as_uuid, sid
from tests._shared.scripted_provider import (
    ScriptedProvider,
    build_tool_call_response,
    make_e2e_identity,
    make_text_response,
)

pytestmark = pytest.mark.unit


def _task() -> Task:
    return Task(
        id=as_uuid("obj-1"),
        title="Build a Tetris web game",
        description="A playable browser Tetris.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="tetris-web",
        created_by="ceo",
    )


def _plan_args() -> dict[str, JsonValue]:
    return {
        "subtasks": [
            {
                "id": "s1",
                "title": "Board renderer",
                "description": "Render the 10x20 grid",
                "expected_artifacts": ["src/board.tsx", "tests/board.test.tsx"],
                "acceptance_criteria": ["grid renders 10x20"],
            },
            {
                "id": "s2",
                "title": "Piece movement",
                "description": "Drop and rotate",
                "dependencies": ["s1"],
                "expected_artifacts": ["src/movement.tsx"],
                "acceptance_criteria": ["pieces drop and rotate"],
            },
        ],
        "task_structure": "sequential",
        "coordination_topology": "auto",
    }


class _SentinelFallback(DecompositionStrategy):
    """Records whether it was invoked and returns a fixed plan."""

    def __init__(self) -> None:
        self.called = False
        self.plan = DecompositionPlan(
            parent_task_id=sid("obj-1"),
            subtasks=(
                SubtaskDefinition(
                    id=sid("fallback-1"),
                    title="Fallback subtask",
                    description="Produced by the fallback strategy",
                ),
            ),
        )

    @override
    async def decompose(
        self, task: Task, context: DecompositionContext
    ) -> DecompositionPlan:
        del task, context
        self.called = True
        return self.plan

    @override
    def get_strategy_name(self) -> str:
        return "sentinel-fallback"


def _strategy(
    provider: ScriptedProvider, fallback: _SentinelFallback
) -> AgentSessionDecompositionStrategy:
    return AgentSessionDecompositionStrategy(
        provider=provider,
        fallback=fallback,
        config=AgentSessionDecompositionConfig(max_turns=4),
    )


class TestAgentSessionDecompose:
    async def test_owner_session_returns_submitted_plan(self) -> None:
        # The owner's session calls submit_decomposition_plan, then ends on a
        # tool-call-free turn; the captured plan is returned with armed fields.
        provider = ScriptedProvider(
            [
                build_tool_call_response("submit_decomposition_plan", _plan_args()),
                make_text_response("Plan submitted."),
            ]
        )
        fallback = _SentinelFallback()
        strategy = _strategy(provider, fallback)
        context = DecompositionContext(owner_identity=make_e2e_identity())

        plan = await strategy.decompose(_task(), context)

        assert not fallback.called
        assert len(plan.subtasks) == 2
        first = plan.subtasks[0]
        # ids remapped to UUIDs, armed fields threaded through.
        assert str(UUID(first.id)) == first.id
        assert first.expected_artifacts == ("src/board.tsx", "tests/board.test.tsx")
        assert first.acceptance_criteria == ("grid renders 10x20",)

    async def test_no_owner_falls_back(self) -> None:
        provider = ScriptedProvider([make_text_response("unused")])
        fallback = _SentinelFallback()
        strategy = _strategy(provider, fallback)

        plan = await strategy.decompose(_task(), DecompositionContext())

        assert fallback.called
        assert plan is fallback.plan
        # The session never ran: the provider was not called.
        assert provider.call_count == 0

    async def test_session_without_submission_falls_back(self) -> None:
        # The owner reasons but never submits a plan; the strategy degrades to
        # the fallback rather than failing the greenlight.
        provider = ScriptedProvider([make_text_response("I am still thinking.")])
        fallback = _SentinelFallback()
        strategy = _strategy(provider, fallback)
        context = DecompositionContext(owner_identity=make_e2e_identity())

        plan = await strategy.decompose(_task(), context)

        assert fallback.called
        assert plan is fallback.plan
        assert provider.call_count >= 1

    def test_strategy_name(self) -> None:
        strategy = _strategy(ScriptedProvider([]), _SentinelFallback())
        assert strategy.get_strategy_name() == "agent-session"


class TestSubmitDecompositionPlanTool:
    async def test_captures_valid_plan(self) -> None:
        capture = _PlanCapture()
        tool = SubmitDecompositionPlanTool(parent_task_id=sid("obj-1"), capture=capture)
        result = await tool.execute(arguments=dict(_plan_args()))
        assert isinstance(result, ToolExecutionResult)
        assert not result.is_error
        assert capture.plan is not None
        assert len(capture.plan.subtasks) == 2

    async def test_rejects_malformed_plan(self) -> None:
        capture = _PlanCapture()
        tool = SubmitDecompositionPlanTool(parent_task_id=sid("obj-1"), capture=capture)
        result = await tool.execute(arguments={"subtasks": "not-a-list"})
        assert result.is_error
        assert capture.plan is None
