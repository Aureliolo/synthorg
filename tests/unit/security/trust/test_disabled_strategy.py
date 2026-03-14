"""Tests for the disabled trust strategy."""

import pytest

from synthorg.core.enums import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.security.trust.disabled_strategy import DisabledTrustStrategy
from synthorg.security.trust.models import TrustState
from tests.unit.security.trust.conftest import make_performance_snapshot

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestDisabledTrustStrategy:
    """Tests for DisabledTrustStrategy."""

    def test_name_property(self) -> None:
        strategy = DisabledTrustStrategy()
        assert strategy.name == "disabled"

    def test_initial_state_creates_state_with_configured_level(self) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=ToolAccessLevel.STANDARD,
        )
        state = strategy.initial_state(
            agent_id=NotBlankStr("agent-001"),
        )
        assert isinstance(state, TrustState)
        assert state.agent_id == "agent-001"
        assert state.global_level == ToolAccessLevel.STANDARD

    def test_initial_state_default_level(self) -> None:
        strategy = DisabledTrustStrategy()
        state = strategy.initial_state(
            agent_id=NotBlankStr("agent-001"),
        )
        assert state.global_level == ToolAccessLevel.STANDARD

    def test_initial_state_sandboxed_level(self) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=ToolAccessLevel.SANDBOXED,
        )
        state = strategy.initial_state(
            agent_id=NotBlankStr("agent-001"),
        )
        assert state.global_level == ToolAccessLevel.SANDBOXED

    async def test_evaluate_returns_current_level_unchanged(self) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=ToolAccessLevel.STANDARD,
        )
        state = strategy.initial_state(
            agent_id=NotBlankStr("agent-001"),
        )
        snapshot = make_performance_snapshot("agent-001", quality=9.0)

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.recommended_level == ToolAccessLevel.STANDARD
        assert result.current_level == ToolAccessLevel.STANDARD
        assert result.should_change is False
        assert result.requires_human_approval is False
        assert result.strategy_name == "disabled"

    async def test_evaluate_never_recommends_change(self) -> None:
        """Even with excellent performance, disabled strategy never changes."""
        strategy = DisabledTrustStrategy(
            initial_level=ToolAccessLevel.SANDBOXED,
        )
        state = strategy.initial_state(
            agent_id=NotBlankStr("agent-001"),
        )
        snapshot = make_performance_snapshot(
            "agent-001",
            quality=10.0,
            success_rate=1.0,
            tasks_completed=100,
        )

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.should_change is False
        assert result.recommended_level == ToolAccessLevel.SANDBOXED

    async def test_evaluate_at_elevated_stays_elevated(self) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=ToolAccessLevel.ELEVATED,
        )
        state = strategy.initial_state(
            agent_id=NotBlankStr("agent-001"),
        )
        snapshot = make_performance_snapshot("agent-001", quality=3.0)

        result = await strategy.evaluate(
            agent_id=NotBlankStr("agent-001"),
            current_state=state,
            snapshot=snapshot,
        )

        assert result.should_change is False
        assert result.recommended_level == ToolAccessLevel.ELEVATED
