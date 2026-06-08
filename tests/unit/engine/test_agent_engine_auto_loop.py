"""Unit tests for AgentEngine auto-loop selection integration."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from synthorg.budget.config import BudgetAlertConfig, BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.tracker import CostTracker
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, TaskStatus, TaskType
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.hybrid_loop import HybridLoop
from synthorg.engine.hybrid_models import HybridLoopConfig
from synthorg.engine.loop_selector import AutoLoopConfig
from synthorg.engine.plan_execute_loop import PlanExecuteLoop
from synthorg.engine.plan_models import PlanExecuteConfig
from synthorg.engine.react_loop import ReactLoop
from synthorg.engine.run_result import AgentRunResult
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_AUTO_SELECTED,
    EXECUTION_LOOP_BUDGET_UNAVAILABLE,
)
from synthorg.providers.models import CompletionResponse
from tests._shared import as_uuid

if TYPE_CHECKING:
    from .conftest import MockCompletionProvider

from .conftest import make_completion_response as _make_completion_response

# ── Helpers ──────────────────────────────────────────────────


def _make_task_with_complexity(
    *,
    complexity: Complexity,
    agent_id: str,
    task_id: str = "task-auto-001",
) -> Task:
    """Build a task with specific complexity for auto-loop tests."""
    return Task(
        id=as_uuid(task_id),
        title="Auto-loop test task",
        description="A task for testing auto-loop selection.",
        type=TaskType.DEVELOPMENT,
        project="proj-001",
        created_by="manager",
        assigned_to=agent_id,
        status=TaskStatus.ASSIGNED,
        estimated_complexity=complexity,
    )


def _make_plan_exec_responses() -> list[CompletionResponse]:
    """Build provider responses for a plan-execute loop run."""
    return [
        _make_completion_response(
            content="1. Implement the feature\nExpected: Feature works correctly",
        ),
        _make_completion_response(content="Done."),
    ]


def _make_hybrid_responses() -> list[CompletionResponse]:
    """Build provider responses for a hybrid loop run."""
    return [
        _make_completion_response(
            content="1. Implement the feature\nExpected: Feature works correctly",
        ),
        _make_completion_response(content="Done."),
        _make_completion_response(
            content='{"summary": "Done", "replan": false}',
        ),
    ]


def _make_budget_enforcer() -> BudgetEnforcer:
    """Build a BudgetEnforcer with standard test config.

    Returns a BudgetEnforcer backed by a fresh CostTracker and a
    BudgetConfig with total_monthly=100, warn_at=70, critical_at=85,
    hard_stop_at=100.
    """
    cfg = BudgetConfig(
        total_monthly=100.0,
        alerts=BudgetAlertConfig(warn_at=70, critical_at=85, hard_stop_at=100),
    )
    tracker = CostTracker(budget_config=cfg)
    return BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)


# ── Auto-loop selection ──────────────────────────────────────


@pytest.mark.unit
class TestAutoLoopSelection:
    """AgentEngine with auto_loop_config selects loop per task complexity."""

    async def test_simple_task_uses_react(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
        )
        task = _make_task_with_complexity(
            complexity=Complexity.SIMPLE,
            agent_id=str(sample_agent_with_personality.id),
        )

        with structlog.testing.capture_logs() as logs:
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=task,
            )

        assert isinstance(result, AgentRunResult)
        selected_events = [
            e for e in logs if e.get("event") == EXECUTION_LOOP_AUTO_SELECTED
        ]
        assert len(selected_events) == 1
        assert selected_events[0]["selected_loop"] == "react"

    async def test_medium_task_uses_plan_execute(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory(_make_plan_exec_responses())
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
        )
        task = _make_task_with_complexity(
            complexity=Complexity.MEDIUM,
            agent_id=str(sample_agent_with_personality.id),
        )

        with structlog.testing.capture_logs() as logs:
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=task,
            )

        assert isinstance(result, AgentRunResult)
        selected_events = [
            e for e in logs if e.get("event") == EXECUTION_LOOP_AUTO_SELECTED
        ]
        assert len(selected_events) == 1
        assert selected_events[0]["selected_loop"] == "plan_execute"


# ── Mutual exclusivity ──────────────────────────────────────


@pytest.mark.unit
class TestAutoLoopWithExplicitLoop:
    """execution_loop and auto_loop_config are mutually exclusive."""

    def test_both_raises_value_error(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        provider = mock_provider_factory([])
        with pytest.raises(ValueError, match="mutually exclusive"):
            AgentEngine(
                provider=provider,
                execution_loop=ReactLoop(),
                auto_loop_config=AutoLoopConfig(),
            )


# ── Budget-aware selection ───────────────────────────────────


@pytest.mark.unit
class TestAutoLoopBudgetAware:
    """Budget state influences loop selection for complex tasks."""

    async def test_complex_tight_budget_uses_plan_execute(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Complex + tight budget => plan_execute (not hybrid)."""
        provider = mock_provider_factory(_make_plan_exec_responses())

        enforcer = _make_budget_enforcer()

        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(budget_tight_threshold=80),
            budget_enforcer=enforcer,
        )

        task = _make_task_with_complexity(
            complexity=Complexity.COMPLEX,
            agent_id=str(sample_agent_with_personality.id),
        )

        # Mock utilization at 90% (above 80% threshold)
        with (
            patch.object(
                enforcer,
                "get_budget_utilization_pct",
                new_callable=AsyncMock,
                return_value=90.0,
            ),
            structlog.testing.capture_logs() as logs,
        ):
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=task,
            )

        assert isinstance(result, AgentRunResult)
        selected_events = [
            e for e in logs if e.get("event") == EXECUTION_LOOP_AUTO_SELECTED
        ]
        assert len(selected_events) == 1
        assert selected_events[0]["selected_loop"] == "plan_execute"

    async def test_complex_ok_budget_uses_hybrid(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Complex + OK budget => hybrid loop selected."""
        provider = mock_provider_factory(_make_hybrid_responses())

        enforcer = _make_budget_enforcer()

        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
            budget_enforcer=enforcer,
        )

        task = _make_task_with_complexity(
            complexity=Complexity.COMPLEX,
            agent_id=str(sample_agent_with_personality.id),
        )

        # Mock utilization at 30% (well below threshold)
        with (
            patch.object(
                enforcer,
                "get_budget_utilization_pct",
                new_callable=AsyncMock,
                return_value=30.0,
            ),
            structlog.testing.capture_logs() as logs,
        ):
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=task,
            )

        assert isinstance(result, AgentRunResult)
        selected_events = [
            e for e in logs if e.get("event") == EXECUTION_LOOP_AUTO_SELECTED
        ]
        assert len(selected_events) == 1
        assert selected_events[0]["selected_loop"] == "hybrid"


# ── Budget error fallback ────────────────────────────────────


@pytest.mark.unit
class TestAutoLoopFallbackOnBudgetError:
    """Budget query failure => proceeds without budget awareness."""

    async def test_budget_unavailable_still_selects_loop(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Budget utilization unknown => proceeds without downgrade."""
        provider = mock_provider_factory(_make_hybrid_responses())

        enforcer = _make_budget_enforcer()

        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
            budget_enforcer=enforcer,
        )

        task = _make_task_with_complexity(
            complexity=Complexity.COMPLEX,
            agent_id=str(sample_agent_with_personality.id),
        )

        # Budget query returns None -> no downgrade, hybrid stays
        with (
            patch.object(
                enforcer,
                "get_budget_utilization_pct",
                new_callable=AsyncMock,
                return_value=None,
            ),
            structlog.testing.capture_logs() as logs,
        ):
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=task,
            )

        assert isinstance(result, AgentRunResult)
        selected_events = [
            e for e in logs if e.get("event") == EXECUTION_LOOP_AUTO_SELECTED
        ]
        assert len(selected_events) == 1
        # Hybrid selected (no budget downgrade since None, no fallback)
        assert selected_events[0]["selected_loop"] == "hybrid"

        # Verify budget-unavailable debug event was emitted
        unavail_events = [
            e for e in logs if e.get("event") == EXECUTION_LOOP_BUDGET_UNAVAILABLE
        ]
        assert len(unavail_events) == 1


# -- Resume path with auto-loop -----------------------------------


@pytest.mark.unit
class TestAutoLoopResumePath:
    """Resume path calls _resolve_loop, not static self._loop."""

    async def test_execute_resumed_loop_calls_resolve_loop(
        self,
        sample_agent_with_personality: AgentIdentity,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """_execute_resumed_loop delegates to _resolve_loop for auto mode."""
        response = _make_completion_response()
        provider = mock_provider_factory([response])
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
        )

        task = _make_task_with_complexity(
            complexity=Complexity.MEDIUM,
            agent_id=str(sample_agent_with_personality.id),
        )
        checkpoint_ctx = AgentContext.from_identity(
            sample_agent_with_personality,
            task=task,
        )

        # Build a mock loop whose execute we can assert on.
        exec_result = MagicMock()
        exec_result.termination_reason = MagicMock()
        exec_result.termination_reason.value = "completed"

        resolved_loop = MagicMock()
        resolved_loop.execute = AsyncMock(return_value=exec_result)
        resolve_mock = AsyncMock(spec=engine._resolve_loop, return_value=resolved_loop)

        with patch.object(engine, "_resolve_loop", resolve_mock):
            await engine._execute_resumed_loop(
                checkpoint_ctx,
                str(sample_agent_with_personality.id),
                str(task.id),
            )

        # _resolve_loop was called with the checkpoint's task + IDs
        resolve_mock.assert_awaited_once()
        call_args = resolve_mock.call_args
        call_task = call_args[0][0]
        assert call_task.estimated_complexity == Complexity.MEDIUM
        assert call_args[0][1] == str(sample_agent_with_personality.id)
        assert call_args[0][2] == str(task.id)

        # The resolved loop instance was actually executed
        resolved_loop.execute.assert_awaited_once()


# -- Config wiring through auto-selection path -------------------


@pytest.mark.unit
class TestAutoLoopConfigWiring:
    """compaction_callback and plan_execute_config are wired through."""

    async def test_compaction_callback_wired_to_react_via_auto_selection(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """SIMPLE task -> ReactLoop receives compaction_callback."""
        provider = mock_provider_factory([])
        compact_cb = AsyncMock()
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
            compaction_callback=compact_cb,
        )
        task = _make_task_with_complexity(
            complexity=Complexity.SIMPLE,
            agent_id="agent-wire-001",
            task_id="task-wire-001",
        )
        loop = await engine._resolve_loop(task, "agent-wire-001", str(task.id))
        assert isinstance(loop, ReactLoop)
        assert loop.compaction_callback is compact_cb

    async def test_compaction_callback_wired_to_plan_execute_via_auto_selection(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """MEDIUM task -> PlanExecuteLoop receives compaction_callback."""
        provider = mock_provider_factory([])
        compact_cb = AsyncMock()
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
            compaction_callback=compact_cb,
        )
        task = _make_task_with_complexity(
            complexity=Complexity.MEDIUM,
            agent_id="agent-wire-002",
            task_id="task-wire-002",
        )
        loop = await engine._resolve_loop(task, "agent-wire-002", str(task.id))
        assert isinstance(loop, PlanExecuteLoop)
        assert loop.compaction_callback is compact_cb

    async def test_compaction_callback_wired_to_hybrid_via_auto_selection(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """COMPLEX task + OK budget -> HybridLoop receives compaction_callback."""
        provider = mock_provider_factory([])
        compact_cb = AsyncMock()
        enforcer = _make_budget_enforcer()
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
            compaction_callback=compact_cb,
            budget_enforcer=enforcer,
        )
        task = _make_task_with_complexity(
            complexity=Complexity.COMPLEX,
            agent_id="agent-wire-003",
            task_id="task-wire-003",
        )
        with patch.object(
            enforcer,
            "get_budget_utilization_pct",
            new_callable=AsyncMock,
            return_value=30.0,
        ):
            loop = await engine._resolve_loop(task, "agent-wire-003", str(task.id))
        assert isinstance(loop, HybridLoop)
        assert loop.compaction_callback is compact_cb

    async def test_plan_execute_config_wired_via_auto_selection(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """MEDIUM task -> PlanExecuteLoop receives plan_execute_config."""
        provider = mock_provider_factory([])
        pe_config = PlanExecuteConfig(max_replans=7)
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
            plan_execute_config=pe_config,
        )
        task = _make_task_with_complexity(
            complexity=Complexity.MEDIUM,
            agent_id="agent-wire-004",
            task_id="task-wire-004",
        )
        loop = await engine._resolve_loop(task, "agent-wire-004", str(task.id))
        assert isinstance(loop, PlanExecuteLoop)
        assert loop.config.max_replans == 7

    def test_compaction_callback_wired_to_default_loop(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Without auto_loop_config, default ReactLoop receives callback."""
        provider = mock_provider_factory([])
        compact_cb = MagicMock()
        engine = AgentEngine(
            provider=provider,
            compaction_callback=compact_cb,
        )
        assert isinstance(engine._loop, ReactLoop)
        assert engine._loop.compaction_callback is compact_cb

    def test_compaction_callback_defaults_to_none(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Omitting compaction_callback leaves loop attribute None."""
        provider = mock_provider_factory([])
        engine = AgentEngine(provider=provider)
        assert isinstance(engine._loop, ReactLoop)
        assert engine._loop.compaction_callback is None

    async def test_hybrid_loop_config_wired_via_auto_selection(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """COMPLEX task + OK budget -> HybridLoop receives hybrid_loop_config."""
        provider = mock_provider_factory([])
        hl_config = HybridLoopConfig(max_plan_steps=3, max_turns_per_step=8)
        enforcer = _make_budget_enforcer()
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
            hybrid_loop_config=hl_config,
            budget_enforcer=enforcer,
        )
        task = _make_task_with_complexity(
            complexity=Complexity.COMPLEX,
            agent_id="agent-wire-005",
            task_id="task-wire-005",
        )
        with patch.object(
            enforcer,
            "get_budget_utilization_pct",
            new_callable=AsyncMock,
            return_value=30.0,
        ):
            loop = await engine._resolve_loop(task, "agent-wire-005", str(task.id))
        assert isinstance(loop, HybridLoop)
        assert loop.config.max_plan_steps == 3
        assert loop.config.max_turns_per_step == 8

    async def test_plan_execute_config_defaults_when_none(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Omitting plan_execute_config uses default PlanExecuteConfig."""
        provider = mock_provider_factory([])
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
        )
        task = _make_task_with_complexity(
            complexity=Complexity.MEDIUM,
            agent_id="agent-wire-006",
            task_id="task-wire-006",
        )
        loop = await engine._resolve_loop(task, "agent-wire-006", str(task.id))
        assert isinstance(loop, PlanExecuteLoop)
        default_config = PlanExecuteConfig()
        assert loop.config.max_replans == default_config.max_replans

    async def test_both_compaction_and_plan_config_wired_simultaneously(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        """Both compaction_callback and plan_execute_config wired together."""
        provider = mock_provider_factory([])
        compact_cb = AsyncMock()
        pe_config = PlanExecuteConfig(max_replans=5)
        engine = AgentEngine(
            provider=provider,
            auto_loop_config=AutoLoopConfig(),
            compaction_callback=compact_cb,
            plan_execute_config=pe_config,
        )
        task = _make_task_with_complexity(
            complexity=Complexity.MEDIUM,
            agent_id="agent-wire-007",
            task_id="task-wire-007",
        )
        loop = await engine._resolve_loop(task, "agent-wire-007", str(task.id))
        assert isinstance(loop, PlanExecuteLoop)
        assert loop.compaction_callback is compact_cb
        assert loop.config.max_replans == 5
