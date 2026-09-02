"""What quota pressure may and may not do to a dispatch.

A provider is a registered connection with its own credentials, endpoint
and quota, so re-pointing an agent at another one mid-run would execute the
operator's choice somewhere nobody chose and bill a quota nobody named.
Degradation therefore waits (QUEUE) or refuses (ALERT); the org's answer to
a connection that stays out is the roster marking the agent unavailable and
reassigning its work, which happens above the engine.

These tests hold that line from both sides: the pre-flight is asked about
the agent's OWN provider, a refusal ends the run, and a registry full of
other connections is never reached for.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.budget.config import BudgetAlertConfig, BudgetConfig
from synthorg.budget.degradation import DegradationResult, PreFlightResult
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.errors import QuotaExhaustedError
from synthorg.budget.quota import DegradationAction
from synthorg.budget.tracker import CostTracker
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import TerminationReason

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task

from dataclasses import replace

from synthorg.providers.registry import ProviderRegistry
from tests._shared import UNWIRED_BUDGET, UNWIRED_ROUTING, engine_with, mock_of

from .conftest import (
    MockCompletionProvider,
    make_completion_response,
)

_AGENT_PROVIDER = "test-provider"
_OTHER_PROVIDER = "some-other-provider"


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

    async def test_the_preflight_is_asked_about_the_agents_own_provider(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Quota is per connection, so the check must name the right one.

        Asking about anything but ``identity.model.provider`` would meter
        one connection while the call hits another.
        """
        enforcer = _make_enforcer()
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = engine_with(
            provider, budget=replace(UNWIRED_BUDGET, budget_enforcer=enforcer)
        )

        with patch.object(
            enforcer,
            "check_can_execute",
            new=AsyncMock(
                spec=enforcer.check_can_execute, return_value=PreFlightResult()
            ),
        ) as mock_check:
            await engine.run(
                identity=sample_agent,
                task=sample_task_with_criteria,
            )

        mock_check.assert_awaited_once()
        assert mock_check.call_args.kwargs.get("provider_name") == _AGENT_PROVIDER

    async def test_a_quota_refusal_stops_the_run(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """ALERT refuses, and refusing is the whole behaviour.

        There is no second connection to try, so the run stops with a
        reason an operator can act on rather than quietly succeeding
        somewhere else.
        """
        enforcer = _make_enforcer()
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = engine_with(
            provider, budget=replace(UNWIRED_BUDGET, budget_enforcer=enforcer)
        )

        with patch.object(
            enforcer,
            "check_can_execute",
            new=AsyncMock(
                spec=enforcer.check_can_execute,
                side_effect=QuotaExhaustedError(
                    "quota exhausted",
                    provider_name=_AGENT_PROVIDER,
                    degradation_action=DegradationAction.ALERT,
                ),
            ),
        ):
            result = await engine.run(
                identity=sample_agent,
                task=sample_task_with_criteria,
            )

        assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
        assert provider.call_count == 0

    async def test_an_exhausted_agent_never_lands_on_another_connection(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A registry full of alternatives is not a menu.

        This is the regression that matters: the deleted design read a
        fallback provider off the degradation result and dispatched there,
        so an exhausted agent silently ran on a connection with its own
        credentials, quota and bill. With a second connection registered
        and reachable, the refusal must still be a refusal.
        """
        enforcer = _make_enforcer()
        agent_provider = MockCompletionProvider(
            [make_completion_response(content="primary")],
        )
        other_provider = MockCompletionProvider(
            [make_completion_response(content="somewhere else")],
        )
        looked_up: list[str] = []

        def _get(name: str) -> MockCompletionProvider:
            looked_up.append(name)
            return agent_provider if name == _AGENT_PROVIDER else other_provider

        engine = engine_with(
            agent_provider,
            routing=replace(
                UNWIRED_ROUTING, provider_registry=mock_of[ProviderRegistry](get=_get)
            ),
            budget=replace(UNWIRED_BUDGET, budget_enforcer=enforcer),
        )

        with patch.object(
            enforcer,
            "check_can_execute",
            new=AsyncMock(
                spec=enforcer.check_can_execute,
                side_effect=QuotaExhaustedError(
                    "quota exhausted",
                    provider_name=_AGENT_PROVIDER,
                    degradation_action=DegradationAction.ALERT,
                ),
            ),
        ):
            result = await engine.run(
                identity=sample_agent,
                task=sample_task_with_criteria,
            )

        assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED
        assert agent_provider.call_count == 0
        assert other_provider.call_count == 0
        assert _OTHER_PROVIDER not in looked_up

    async def test_a_queued_wait_dispatches_to_the_same_connection(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """QUEUE waits for the window and then runs where it always would."""
        enforcer = _make_enforcer()
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = engine_with(
            provider, budget=replace(UNWIRED_BUDGET, budget_enforcer=enforcer)
        )

        queue_result = PreFlightResult(
            degradation=DegradationResult(
                provider=NotBlankStr(_AGENT_PROVIDER),
                action_taken=DegradationAction.QUEUE,
                wait_seconds=30.0,
            ),
        )

        with patch.object(
            enforcer,
            "check_can_execute",
            new=AsyncMock(spec=enforcer.check_can_execute, return_value=queue_result),
        ):
            result = await engine.run(
                identity=sample_agent,
                task=sample_task_with_criteria,
            )

        assert provider.call_count == 1
        assert result.termination_reason is not TerminationReason.BUDGET_EXHAUSTED

    async def test_the_binding_the_run_executes_under_is_the_one_handed_in(
        self,
        sample_agent: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Budget pressure tunes nothing about the pair.

        The dispatched call carries the agent's own model id, so no layer
        between the roster and the driver rewrote it on the way through.
        """
        enforcer = _make_enforcer()
        provider = MockCompletionProvider(
            [make_completion_response(content="Done.")],
        )
        engine = engine_with(
            provider, budget=replace(UNWIRED_BUDGET, budget_enforcer=enforcer)
        )

        with patch.object(
            enforcer,
            "check_can_execute",
            new=AsyncMock(
                spec=enforcer.check_can_execute, return_value=PreFlightResult()
            ),
        ):
            await engine.run(
                identity=sample_agent,
                task=sample_task_with_criteria,
            )

        assert provider.recorded_models == [sample_agent.model.model_id]
