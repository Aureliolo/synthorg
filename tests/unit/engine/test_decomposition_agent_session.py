"""Tests for the owner-run agent-session decomposition strategy."""

from types import SimpleNamespace
from typing import override
from uuid import UUID

import pytest
import structlog.testing
from pydantic import JsonValue, ValidationError

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
from synthorg.engine.decomposition.tool_provider import DecompositionToolProvider
from synthorg.engine.errors import (
    DecompositionDepthError,
    DecompositionSubtaskLimitError,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
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
                "stakes": "normal",
                "required_role": "Frontend Engineer",
                "expected_artifacts": ["src/board.tsx", "tests/board.test.tsx"],
                "acceptance_criteria": ["grid renders 10x20"],
            },
            {
                "id": "s2",
                "title": "Piece movement",
                "description": "Drop and rotate",
                "dependencies": ["s1"],
                "stakes": "normal",
                "required_role": "Frontend Engineer",
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
                    expected_artifacts=("src/fallback.py",),
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
        provider_selector=lambda _identity: provider,
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

    async def test_owner_provider_unresolved_falls_back(self) -> None:
        # The owner is pinned to a provider the registry does not know, so the
        # selector raises; the strategy falls back rather than dispatching to a
        # default gateway.
        from synthorg.providers.errors import DriverNotRegisteredError

        def _raise(_identity: object) -> ScriptedProvider:
            msg = "owner provider not registered"
            raise DriverNotRegisteredError(msg, context={"provider": "ghost"})

        fallback = _SentinelFallback()
        strategy = AgentSessionDecompositionStrategy(
            provider_selector=_raise,
            fallback=fallback,
            config=AgentSessionDecompositionConfig(max_turns=4),
        )
        context = DecompositionContext(owner_identity=make_e2e_identity())

        plan = await strategy.decompose(_task(), context)

        assert fallback.called
        assert plan is fallback.plan

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

    async def test_double_submit_overwrites_with_latest(self) -> None:
        capture = _PlanCapture()
        tool = SubmitDecompositionPlanTool(parent_task_id=sid("obj-1"), capture=capture)
        await tool.execute(arguments=dict(_plan_args()))
        assert capture.plan is not None
        assert len(capture.plan.subtasks) == 2
        # A second submission supersedes the first (the agent revised).
        single: dict[str, object] = {
            "subtasks": [
                {
                    "id": "s1",
                    "title": "Only subtask",
                    "description": "d",
                    "expected_artifacts": ["src/only.tsx"],
                    "acceptance_criteria": ["works"],
                }
            ],
            "task_structure": "sequential",
            "coordination_topology": "auto",
        }
        await tool.execute(arguments=single)
        assert capture.plan is not None
        assert len(capture.plan.subtasks) == 1


class _FixedTool(BaseTool):
    """A no-op tool whose action type is derived from its category."""

    def __init__(self, *, name: str, category: ToolCategory) -> None:
        super().__init__(
            name=name,
            description=f"{name} tool",
            parameters_schema={"type": "object", "properties": {}},
            category=category,
        )

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        del arguments
        return ToolExecutionResult(content="ok")


class _ListToolProvider:
    """Decomposition tool provider returning a fixed tool list."""

    def __init__(self, tools: tuple[BaseTool, ...]) -> None:
        self._tools = tools

    def build_tools(
        self, *, owner_id: str, project_id: str | None
    ) -> tuple[BaseTool, ...]:
        del owner_id, project_id
        return self._tools


class TestReadOnlyToolBoundary:
    def test_write_tools_are_dropped(self) -> None:
        # MEMORY -> memory:read (read-only); VERSION_CONTROL -> vcs:commit
        # (write). Only the read-only tool survives into the session.
        provider: DecompositionToolProvider = _ListToolProvider(
            (
                _FixedTool(name="recall", category=ToolCategory.MEMORY),
                _FixedTool(name="commit", category=ToolCategory.VERSION_CONTROL),
            )
        )
        strategy = AgentSessionDecompositionStrategy(
            provider_selector=lambda _identity: ScriptedProvider([]),
            fallback=_SentinelFallback(),
            tool_provider=provider,
        )
        kept = strategy._planning_tools(_task(), make_e2e_identity())
        assert [tool.name for tool in kept] == ["recall"]

    def test_no_provider_yields_no_planning_tools(self) -> None:
        strategy = _strategy(ScriptedProvider([]), _SentinelFallback())
        assert strategy._planning_tools(_task(), make_e2e_identity()) == ()


class TestAgentSessionGuards:
    async def test_over_max_subtasks_raises_rather_than_falling_back(self) -> None:
        # The session submits 2 subtasks but the context caps at 1. The
        # researched plan is surfaced as a failure the operator can act on,
        # not swapped for the single-shot fallback's thinner one.
        provider = ScriptedProvider(
            [
                build_tool_call_response("submit_decomposition_plan", _plan_args()),
                make_text_response("done"),
            ]
        )
        fallback = _SentinelFallback()
        strategy = _strategy(provider, fallback)
        context = DecompositionContext(
            owner_identity=make_e2e_identity(), max_subtasks=1
        )

        with pytest.raises(DecompositionSubtaskLimitError) as excinfo:
            await strategy.decompose(_task(), context)

        assert not fallback.called
        # The reason reaches the durable plan verbatim, so it has to name
        # both numbers or the operator cannot tell how far over it was.
        assert "2 subtasks" in str(excinfo.value)
        assert "max_subtasks of 1" in str(excinfo.value)

    async def test_a_session_submitting_no_plan_still_falls_back(self) -> None:
        # Nothing was researched, so there is no better plan to lose.
        provider = ScriptedProvider([make_text_response("I give up")])
        fallback = _SentinelFallback()
        strategy = _strategy(provider, fallback)
        context = DecompositionContext(owner_identity=make_e2e_identity())

        plan = await strategy.decompose(_task(), context)

        assert fallback.called
        assert plan is fallback.plan

    async def test_a_failed_session_does_not_log_the_raw_failure_text(self) -> None:
        """The termination detail is provider text, so it can carry a secret.

        The loop composes it from whatever the provider raised, which for an
        auth failure routinely embeds the credential that failed.
        """
        provider = ScriptedProvider(
            error=RuntimeError("upstream refused: bearer sk-live-abcdef123456")
        )
        fallback = _SentinelFallback()
        strategy = _strategy(provider, fallback)
        context = DecompositionContext(owner_identity=make_e2e_identity())

        with structlog.testing.capture_logs() as events:
            plan = await strategy.decompose(_task(), context)

        assert plan is fallback.plan
        details = [
            event["termination_detail"]
            for event in events
            if "termination_detail" in event
        ]
        assert details, "the no-plan path did not log a termination detail"
        assert all("sk-live-abcdef123456" not in str(d) for d in details)
        assert any("bearer ***" in str(d) for d in details)

    async def test_a_within_limit_plan_is_returned_unchanged(self) -> None:
        provider = ScriptedProvider(
            [
                build_tool_call_response("submit_decomposition_plan", _plan_args()),
                make_text_response("done"),
            ]
        )
        fallback = _SentinelFallback()
        strategy = _strategy(provider, fallback)
        context = DecompositionContext(
            owner_identity=make_e2e_identity(), max_subtasks=2
        )

        plan = await strategy.decompose(_task(), context)

        assert not fallback.called
        assert len(plan.subtasks) == 2

    async def test_depth_limit_raises(self) -> None:
        strategy = _strategy(ScriptedProvider([]), _SentinelFallback())
        context = DecompositionContext(
            owner_identity=make_e2e_identity(),
            current_depth=3,
            max_depth=3,
        )
        with pytest.raises(DecompositionDepthError):
            await strategy.decompose(_task(), context)

    def test_budget_checker_halts_at_ceiling(self) -> None:
        strategy = AgentSessionDecompositionStrategy(
            provider_selector=lambda _identity: ScriptedProvider([]),
            fallback=_SentinelFallback(),
            config=AgentSessionDecompositionConfig(cost_ceiling=1.5),
        )
        checker = strategy._budget_checker()
        below = SimpleNamespace(accumulated_cost=SimpleNamespace(cost=1.0))
        at_ceiling = SimpleNamespace(accumulated_cost=SimpleNamespace(cost=1.5))
        assert checker(below) is False  # type: ignore[arg-type]
        assert checker(at_ceiling) is True  # type: ignore[arg-type]


class TestAgentSessionConfig:
    def test_rejects_out_of_range_turns(self) -> None:
        with pytest.raises(ValidationError):
            AgentSessionDecompositionConfig(max_turns=100)

    def test_rejects_non_positive_ceiling(self) -> None:
        with pytest.raises(ValidationError):
            AgentSessionDecompositionConfig(cost_ceiling=0.0)
