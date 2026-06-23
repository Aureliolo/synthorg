"""Tests for the api-layer meeting/strategy dispatch hooks.

Covers the three builders that bind ``engine.strategy`` subsystems behind
the meeting hook signatures: consensus-velocity detection, premortem
rendering, and the progressive-tier token-budget scaler.
"""

import pytest

from synthorg.api._meeting_strategy_dispatch import (
    build_budget_scaler,
    build_consensus_hook,
    build_premortem_hook,
)
from synthorg.communication.meeting.models import AgentResponse
from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.models import (
    CostTierPreset,
    StrategyConfig,
)


@pytest.mark.unit
class TestConsensusHook:
    def test_flags_prematurely_converged_positions(self) -> None:
        hook = build_consensus_hook(StrategyConfig())
        identical = ("We should ship it now.",) * 4
        assert hook(identical) is True

    def test_diverse_positions_not_flagged(self) -> None:
        hook = build_consensus_hook(StrategyConfig())
        diverse = (
            "Ship immediately, the upside is huge.",
            "Absolutely not, the data migration is far too risky.",
            "Delay a week and run a staged canary first.",
            "Cancel the project, it duplicates the billing rewrite.",
        )
        assert hook(diverse) is False


@pytest.mark.unit
class TestBudgetScaler:
    def test_moderate_tier_is_identity(self) -> None:
        scaler = build_budget_scaler(StrategyConfig(cost_tier=CostTierPreset.MODERATE))
        assert scaler(2000) == 2000

    def test_generous_tier_doubles(self) -> None:
        scaler = build_budget_scaler(StrategyConfig(cost_tier=CostTierPreset.GENEROUS))
        assert scaler(2000) == 4000

    def test_minimal_tier_halves(self) -> None:
        scaler = build_budget_scaler(StrategyConfig(cost_tier=CostTierPreset.MINIMAL))
        assert scaler(2000) == 1000

    def test_scaler_floor_is_one(self) -> None:
        scaler = build_budget_scaler(StrategyConfig(cost_tier=CostTierPreset.MINIMAL))
        assert scaler(1) == 1


@pytest.mark.unit
class TestPremortemHook:
    async def test_renders_section_from_responses(self) -> None:
        async def _caller(
            agent_id: str,
            prompt: str,
            max_tokens: int,
            meeting_id: str,
        ) -> AgentResponse:
            del prompt, max_tokens, meeting_id
            return AgentResponse(
                agent_id=agent_id,
                content=(
                    "This could fail badly if the rollout assumption about "
                    "traffic is wrong and the cache stampedes."
                ),
                input_tokens=10,
                output_tokens=20,
            )

        hook = build_premortem_hook(StrategyConfig())
        result = await hook(
            synthesis_text="Ship the new caching layer on Friday.",
            participant_ids=("agent-a", "agent-b"),
            agent_caller=_caller,
            token_budget=1000,
            context_id="mtg-1",
        )

        assert "Failure modes" in result.text
        # Token usage from the premortem agent calls is aggregated onto the
        # result so the meeting layer can fold it into the budget/minutes.
        assert result.input_tokens >= 10
        assert result.output_tokens >= 20

    async def test_empty_when_no_signal(self) -> None:
        async def _caller(
            agent_id: str,
            prompt: str,
            max_tokens: int,
            meeting_id: str,
        ) -> AgentResponse:
            del prompt, max_tokens, meeting_id
            # Too short to clear the executor's min-length filter.
            return AgentResponse(
                agent_id=agent_id,
                content="ok",
                input_tokens=1,
                output_tokens=1,
            )

        hook = build_premortem_hook(StrategyConfig())
        result = await hook(
            synthesis_text="Ship it.",
            participant_ids=(NotBlankStr("agent-a"),),
            agent_caller=_caller,
            token_budget=1000,
            context_id="mtg-1",
        )

        assert result.text == ""
        # Even when nothing surfaces, the tokens the agent calls consumed
        # are still reported so they are not silently dropped from the budget.
        assert result.input_tokens >= 1
