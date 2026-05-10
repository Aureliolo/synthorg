"""Tests for AgentEngine quota degradation integration."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.budget.config import BudgetAlertConfig, BudgetConfig
from synthorg.budget.degradation import DegradationResult, PreFlightResult
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.quota import DegradationAction
from synthorg.budget.tracker import CostTracker
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_protocol import TerminationReason

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task

from synthorg.providers.registry import ProviderRegistry

from .conftest import (
    MockCompletionProvider,
    make_completion_response,
)


def _make_budget_config() -> BudgetConfig:
    return BudgetConfig(
        total_monthly=100.0,
        alerts=BudgetAlertConfig(warn_at=75, critical_at=90, hard_stop_at=100),
    )


def _make_enforcer(**kwargs: object) -> BudgetEnforcer:
    cfg = _make_budget_config()
    tracker = CostTracker(budget_config=cfg)
    return BudgetEnforcer(
        budget_config=cfg,
        cost_tracker=tracker,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.unit
class TestEngineDegradation:
    """Tests for engine-level degradation handling."""

    async def test_engine_passes_provider_name(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Engine passes identity.model.provider to check_can_execute."""
        enforcer = _make_enforcer()
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(provider=provider, budget_enforcer=enforcer)

        with (
            patch.object(
                enforcer,
                "check_can_execute",
                new=AsyncMock(
                    spec=enforcer.check_can_execute, return_value=PreFlightResult()
                ),
            ) as mock_check,
            patch.object(
                enforcer,
                "resolve_model",
                new=AsyncMock(
                    spec=enforcer.resolve_model,
                    return_value=sample_agent_with_personality,
                ),
            ),
        ):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        # Verify provider_name was passed
        mock_check.assert_awaited_once()
        call_kwargs = mock_check.call_args
        assert call_kwargs.kwargs.get("provider_name") == "test-provider"

    async def test_engine_fallback_raises_without_registry(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Fallback to different provider raises without registry."""
        enforcer = _make_enforcer()
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        # No provider_registry
        engine = AgentEngine(provider=provider, budget_enforcer=enforcer)

        fallback_result = PreFlightResult(
            degradation=DegradationResult(
                original_provider="test-provider",
                effective_provider="fallback-provider",
                action_taken=DegradationAction.FALLBACK,
            ),
        )

        with patch.object(
            enforcer,
            "check_can_execute",
            new=AsyncMock(
                spec=enforcer.check_can_execute, return_value=fallback_result
            ),
        ):
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        # Should result in BUDGET_EXHAUSTED since no registry
        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED
        assert provider.call_count == 0

    async def test_engine_fallback_uses_registry_provider(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Fallback looks up provider from registry."""
        enforcer = _make_enforcer()
        primary_provider = MockCompletionProvider(
            [make_completion_response(content="primary")],
        )
        fallback_provider = MockCompletionProvider(
            [make_completion_response(content="fallback")],
        )

        # Mock registry
        mock_registry = AsyncMock(spec=ProviderRegistry)
        mock_registry.get = lambda name: (
            fallback_provider if name == "fallback-provider" else primary_provider
        )

        engine = AgentEngine(
            provider=primary_provider,
            budget_enforcer=enforcer,
            provider_registry=mock_registry,
        )

        fallback_result = PreFlightResult(
            degradation=DegradationResult(
                original_provider="test-provider",
                effective_provider="fallback-provider",
                action_taken=DegradationAction.FALLBACK,
            ),
        )

        with (
            patch.object(
                enforcer,
                "check_can_execute",
                new=AsyncMock(
                    spec=enforcer.check_can_execute, return_value=fallback_result
                ),
            ),
            patch.object(
                enforcer,
                "resolve_model",
                new=AsyncMock(
                    spec=enforcer.resolve_model, side_effect=lambda ident: ident
                ),
            ),
        ):
            await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        # Fallback provider should have been used
        assert fallback_provider.call_count == 1
        assert primary_provider.call_count == 0

    async def test_engine_queue_no_provider_change(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """QUEUE result uses same provider (no switch needed)."""
        enforcer = _make_enforcer()
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = AgentEngine(provider=provider, budget_enforcer=enforcer)

        queue_result = PreFlightResult(
            degradation=DegradationResult(
                original_provider="test-provider",
                effective_provider="test-provider",
                action_taken=DegradationAction.QUEUE,
                wait_seconds=30.0,
            ),
        )

        with (
            patch.object(
                enforcer,
                "check_can_execute",
                new=AsyncMock(
                    spec=enforcer.check_can_execute, return_value=queue_result
                ),
            ),
            patch.object(
                enforcer,
                "resolve_model",
                new=AsyncMock(
                    spec=enforcer.resolve_model,
                    return_value=sample_agent_with_personality,
                ),
            ),
        ):
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        # Same provider used, execution completes normally
        assert provider.call_count == 1
        assert result.termination_reason != TerminationReason.BUDGET_EXHAUSTED

    async def test_engine_fallback_registry_error_raises(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Registry.get() raising an error results in BUDGET_EXHAUSTED."""
        from synthorg.providers.errors import DriverNotRegisteredError

        enforcer = _make_enforcer()
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )

        mock_registry = AsyncMock(spec=ProviderRegistry)
        mock_registry.get = lambda name: (_ for _ in ()).throw(
            DriverNotRegisteredError(f"No driver for {name!r}"),
        )

        engine = AgentEngine(
            provider=provider,
            budget_enforcer=enforcer,
            provider_registry=mock_registry,
        )

        fallback_result = PreFlightResult(
            degradation=DegradationResult(
                original_provider="test-provider",
                effective_provider="missing-provider",
                action_taken=DegradationAction.FALLBACK,
            ),
        )

        with patch.object(
            enforcer,
            "check_can_execute",
            new=AsyncMock(
                spec=enforcer.check_can_execute, return_value=fallback_result
            ),
        ):
            result = await engine.run(
                identity=sample_agent_with_personality,
                task=sample_task_with_criteria,
            )

        assert result.termination_reason == TerminationReason.BUDGET_EXHAUSTED
        assert provider.call_count == 0
