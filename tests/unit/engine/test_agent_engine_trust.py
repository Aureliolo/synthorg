"""Tests for progressive-trust enforcement at the tool-invoker seam.

Proves the engine narrows an agent's effective tool permissions to
its earned trust level (auto-initialised on first sight), and that a
DISABLED trust strategy (no TrustService wired) is a no-op -- i.e. a
trust-strategy switch changes enforcement behaviour.
"""

import pytest

from synthorg.core.agent import ToolPermissions
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.engine.agent_engine import AgentEngine
from synthorg.security.trust.config import (
    TrustConfig,
    TrustThreshold,
    WeightedTrustWeights,
)
from synthorg.security.trust.enums import TrustStrategyType
from synthorg.security.trust.factory import build_trust_strategy
from synthorg.security.trust.service import TrustService

from .conftest import MockCompletionProvider, make_assignment_agent

pytestmark = pytest.mark.unit


def _weighted_trust_service(initial_level: ToolAccessLevel) -> TrustService:
    config = TrustConfig(
        strategy=TrustStrategyType.WEIGHTED,
        initial_level=initial_level,
        weights=WeightedTrustWeights(),
        promotion_thresholds={
            "standard_to_elevated": TrustThreshold(
                score=0.9,
                requires_human_approval=True,
            ),
        },
    )
    strategy = build_trust_strategy(config)
    assert strategy is not None
    return TrustService(strategy=strategy, config=config)


class TestTrustNarrowing:
    """Trust strategy switch changes the agent's effective tool access."""

    def test_disabled_strategy_is_noop(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        identity = make_assignment_agent("dev").model_copy(
            update={"tools": ToolPermissions(access_level=ToolAccessLevel.ELEVATED)},
        )
        # No trust_service => trust strategy DISABLED.
        engine = AgentEngine(provider=mock_provider_factory([]))

        effective = engine._trust_narrowed_tools(identity)

        assert effective is identity.tools
        assert effective.access_level == ToolAccessLevel.ELEVATED

    def test_trust_narrows_below_identity_level(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        identity = make_assignment_agent("dev").model_copy(
            update={"tools": ToolPermissions(access_level=ToolAccessLevel.ELEVATED)},
        )
        trust = _weighted_trust_service(ToolAccessLevel.STANDARD)
        engine = AgentEngine(
            provider=mock_provider_factory([]),
            trust_service=trust,
        )

        effective = engine._trust_narrowed_tools(identity)

        # Auto-initialised at STANDARD < identity ELEVATED -> narrowed.
        assert effective.access_level == ToolAccessLevel.STANDARD
        # The agent's trust state was seeded on first sight.
        assert trust.get_trust_state(str(identity.id)) is not None

    def test_trust_does_not_grant_above_identity(
        self,
        mock_provider_factory: type[MockCompletionProvider],
    ) -> None:
        identity = make_assignment_agent("dev").model_copy(
            update={
                "tools": ToolPermissions(access_level=ToolAccessLevel.SANDBOXED),
            },
        )
        trust = _weighted_trust_service(ToolAccessLevel.ELEVATED)
        engine = AgentEngine(
            provider=mock_provider_factory([]),
            trust_service=trust,
        )

        effective = engine._trust_narrowed_tools(identity)

        # Trust higher than identity must not widen access.
        assert effective.access_level == ToolAccessLevel.SANDBOXED
