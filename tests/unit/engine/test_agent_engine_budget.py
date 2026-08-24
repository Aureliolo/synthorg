"""Tests for AgentEngine budget enforcer integration."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.budget.config import (
    BudgetAlertConfig,
    BudgetConfig,
)
from synthorg.budget.degradation import PreFlightResult
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.errors import (
    BudgetExhaustedError,
    DailyLimitExceededError,
    QuotaExhaustedError,
)
from synthorg.budget.tracker import CostTracker
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_protocol import TerminationReason

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task

from .conftest import (
    MockCompletionProvider,
    make_completion_response,
)


def _make_budget_config(
    *,
    total_monthly: float = 100.0,
    hard_stop_at: int = 100,
) -> BudgetConfig:
    return BudgetConfig(
        total_monthly=total_monthly,
        alerts=BudgetAlertConfig(
            warn_at=75,
            critical_at=90,
            hard_stop_at=hard_stop_at,
        ),
    )


@pytest.mark.unit
class TestEngineWithEnforcer:
    """Tests for AgentEngine with budget_enforcer wired in."""

    @pytest.mark.parametrize(
        ("exc_cls", "msg"),
        [
            (BudgetExhaustedError, "Monthly budget exhausted"),
            (DailyLimitExceededError, "Daily limit exceeded"),
            (QuotaExhaustedError, "Provider quota exhausted"),
        ],
        ids=["monthly_exhausted", "daily_limit", "quota_exhausted"],
    )
    async def test_preflight_budget_stop_returns_budget_exhausted(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
        exc_cls: type[BudgetExhaustedError],
        msg: str,
    ) -> None:
        """Pre-flight budget errors propagate as BUDGET_EXHAUSTED result."""
        cfg = _make_budget_config(total_monthly=100.0)
        tracker = CostTracker(budget_config=cfg)
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(
            provider=provider,
            budget_enforcer=enforcer,
        )

        with patch.object(
            enforcer,
            "check_can_execute",
            new=AsyncMock(spec=enforcer.check_can_execute, side_effect=exc_cls(msg)),
        ):
            result = await engine.run(
                identity=sample_agent,
                task=sample_task_with_criteria,
            )

        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED
        assert provider.call_count == 0

    async def test_the_enforcer_cannot_re_point_the_run_at_another_model(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Budget pressure refuses spend; it never re-binds the agent.

        The enforcer used to hand the run a rewritten identity at the task
        boundary, so an agent silently ran on a model its operator never
        chose and its capability rung stopped meaning anything. It now has
        no such seam: the call carries the roster's own model id, and the
        only budget lever left is refusal.
        """
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(
            provider=provider,
            budget_enforcer=enforcer,
        )

        assert not hasattr(enforcer, "resolve_model")

        with (
            patch.object(
                enforcer,
                "check_can_execute",
                new=AsyncMock(
                    spec=enforcer.check_can_execute, return_value=PreFlightResult()
                ),
            ),
            patch.object(
                enforcer,
                "make_budget_checker",
                new=AsyncMock(spec=enforcer.make_budget_checker, return_value=None),
            ),
        ):
            result = await engine.run(
                identity=sample_agent,
                task=sample_task_with_criteria,
            )

        assert result.termination_reason == TerminationReason.COMPLETED
        assert provider.recorded_models == [sample_agent.model.model_id]

    async def test_no_enforcer_uses_fallback_checker(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Without enforcer, uses existing make_budget_checker fallback."""
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent,
            task=sample_task_with_criteria,
        )

        assert result.termination_reason == TerminationReason.COMPLETED

    async def test_enforcer_provides_cost_tracker(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """When no explicit cost_tracker, uses enforcer's tracker."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)
        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(
            provider=provider,
            budget_enforcer=enforcer,
        )

        # Run a task and verify costs were recorded to the enforcer's tracker
        with (
            patch.object(
                enforcer,
                "check_can_execute",
                new=AsyncMock(
                    spec=enforcer.check_can_execute, return_value=PreFlightResult()
                ),
            ),
            patch.object(
                enforcer,
                "make_budget_checker",
                new=AsyncMock(spec=enforcer.make_budget_checker, return_value=None),
            ),
        ):
            result = await engine.run(
                identity=sample_agent,
                task=sample_task_with_criteria,
            )

        assert result.termination_reason == TerminationReason.COMPLETED
        # Verify cost was recorded to the enforcer's tracker
        total = await tracker.get_total_cost()
        assert total > 0
