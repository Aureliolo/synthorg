"""Tests for AgentEngine budget enforcer integration."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from ai_company.budget.config import (
    BudgetAlertConfig,
    BudgetConfig,
)
from ai_company.budget.enforcer import BudgetEnforcer
from ai_company.budget.tracker import CostTracker
from ai_company.engine.agent_engine import AgentEngine
from ai_company.engine.errors import BudgetExhaustedError
from ai_company.engine.loop_protocol import TerminationReason

if TYPE_CHECKING:
    from ai_company.core.agent import AgentIdentity
    from ai_company.core.task import Task

from .conftest import (
    MockCompletionProvider,
    make_completion_response,
)

pytestmark = pytest.mark.timeout(30)


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

    async def test_preflight_budget_exhausted_returns_error(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Pre-flight BudgetExhaustedError propagates as error result."""
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
            new=AsyncMock(
                side_effect=BudgetExhaustedError("Monthly budget exhausted"),
            ),
        ):
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        assert result.termination_reason == TerminationReason.ERROR
        assert "BudgetExhaustedError" in (result.execution_result.error_message or "")
        assert provider.call_count == 0

    async def test_model_downgrade_applied(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Model downgrade at task boundary changes model used."""
        cfg = _make_budget_config()
        tracker = CostTracker(budget_config=cfg)

        downgraded_identity = sample_agent_with_personality.model_copy(
            update={
                "model": sample_agent_with_personality.model.model_copy(
                    update={"model_id": "test-small-001"},
                ),
            },
        )

        enforcer = BudgetEnforcer(budget_config=cfg, cost_tracker=tracker)

        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(
            provider=provider,
            budget_enforcer=enforcer,
        )

        with (
            patch.object(
                enforcer,
                "check_can_execute",
                new=AsyncMock(),
            ),
            patch.object(
                enforcer,
                "resolve_model",
                new=AsyncMock(return_value=downgraded_identity),
            ),
            patch.object(
                enforcer,
                "make_budget_checker",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        assert result.termination_reason == TerminationReason.COMPLETED
        # Verify the downgraded model was used for the LLM call
        assert provider.recorded_models[0] == "test-small-001"

    async def test_no_enforcer_uses_fallback_checker(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Without enforcer, uses existing make_budget_checker fallback."""
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(provider=provider)

        result = await engine.run(
            identity=sample_agent_with_personality,
            task=sample_task_with_criteria,
        )

        assert result.termination_reason == TerminationReason.COMPLETED

    async def test_enforcer_provides_cost_tracker(
        self,
        sample_agent_with_personality: AgentIdentity,
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

        # Engine should use enforcer's tracker for cost recording
        assert engine._cost_tracker is tracker
