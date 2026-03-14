"""Tests for the trust service orchestrator."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.enums import ToolAccessLevel
from synthorg.core.types import NotBlankStr
from synthorg.security.trust.disabled_strategy import DisabledTrustStrategy
from synthorg.security.trust.enums import TrustChangeReason
from synthorg.security.trust.errors import TrustEvaluationError
from synthorg.security.trust.models import (
    TrustEvaluationResult,
    TrustState,
)
from synthorg.security.trust.service import TrustService
from synthorg.security.trust.weighted_strategy import WeightedTrustStrategy
from tests.unit.security.trust.conftest import make_performance_snapshot

if TYPE_CHECKING:
    from synthorg.security.trust.config import TrustConfig

pytestmark = pytest.mark.timeout(30)


# ── initialize_agent ─────────────────────────────────────────────


@pytest.mark.unit
class TestInitializeAgent:
    """Tests for TrustService.initialize_agent."""

    def test_creates_state(self, trust_config: TrustConfig) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )

        state = service.initialize_agent(NotBlankStr("agent-001"))

        assert isinstance(state, TrustState)
        assert state.agent_id == "agent-001"
        assert state.global_level == ToolAccessLevel.STANDARD

    def test_state_retrievable_after_init(
        self,
        trust_config: TrustConfig,
    ) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )

        service.initialize_agent(NotBlankStr("agent-001"))
        state = service.get_trust_state(NotBlankStr("agent-001"))

        assert state is not None
        assert state.agent_id == "agent-001"

    def test_multiple_agents(self, trust_config: TrustConfig) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )

        service.initialize_agent(NotBlankStr("agent-001"))
        service.initialize_agent(NotBlankStr("agent-002"))

        s1 = service.get_trust_state(NotBlankStr("agent-001"))
        s2 = service.get_trust_state(NotBlankStr("agent-002"))

        assert s1 is not None
        assert s2 is not None
        assert s1.agent_id == "agent-001"
        assert s2.agent_id == "agent-002"


# ── evaluate_agent ───────────────────────────────────────────────


@pytest.mark.unit
class TestEvaluateAgent:
    """Tests for TrustService.evaluate_agent."""

    async def test_delegates_to_strategy(
        self,
        trust_config: TrustConfig,
    ) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )
        service.initialize_agent(NotBlankStr("agent-001"))
        snapshot = make_performance_snapshot("agent-001")

        result = await service.evaluate_agent(
            NotBlankStr("agent-001"),
            snapshot,
        )

        assert result.strategy_name == "disabled"
        assert result.should_change is False

    async def test_raises_for_unknown_agent(
        self,
        trust_config: TrustConfig,
    ) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )
        snapshot = make_performance_snapshot("unknown")

        with pytest.raises(
            TrustEvaluationError,
            match="not initialized",
        ):
            await service.evaluate_agent(
                NotBlankStr("unknown"),
                snapshot,
            )

    async def test_updates_last_evaluated_at(
        self,
        trust_config: TrustConfig,
    ) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )
        service.initialize_agent(NotBlankStr("agent-001"))
        snapshot = make_performance_snapshot("agent-001")

        await service.evaluate_agent(
            NotBlankStr("agent-001"),
            snapshot,
        )

        state = service.get_trust_state(NotBlankStr("agent-001"))
        assert state is not None
        assert state.last_evaluated_at is not None


# ── apply_trust_change ───────────────────────────────────────────


@pytest.mark.unit
class TestApplyTrustChange:
    """Tests for TrustService.apply_trust_change."""

    async def test_creates_record_when_change(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        strategy = WeightedTrustStrategy(config=weighted_config)
        service = TrustService(
            strategy=strategy,
            config=weighted_config,
        )
        service.initialize_agent(NotBlankStr("agent-001"))

        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.RESTRICTED,
            current_level=ToolAccessLevel.SANDBOXED,
            score=0.75,
            strategy_name=NotBlankStr("weighted"),
        )

        record = await service.apply_trust_change(
            NotBlankStr("agent-001"),
            result,
        )

        assert record is not None
        assert record.old_level == ToolAccessLevel.SANDBOXED
        assert record.new_level == ToolAccessLevel.RESTRICTED
        assert record.reason == TrustChangeReason.SCORE_THRESHOLD

    async def test_returns_none_when_no_change(
        self,
        trust_config: TrustConfig,
    ) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )
        service.initialize_agent(NotBlankStr("agent-001"))

        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.STANDARD,
            current_level=ToolAccessLevel.STANDARD,
            strategy_name=NotBlankStr("disabled"),
        )

        record = await service.apply_trust_change(
            NotBlankStr("agent-001"),
            result,
        )

        assert record is None

    async def test_raises_when_human_approval_required_without_store(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        """No approval store → TrustEvaluationError on human-approval path."""
        strategy = WeightedTrustStrategy(config=weighted_config)
        service = TrustService(
            strategy=strategy,
            config=weighted_config,
        )
        service.initialize_agent(NotBlankStr("agent-001"))

        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.ELEVATED,
            current_level=ToolAccessLevel.STANDARD,
            requires_human_approval=True,
            strategy_name=NotBlankStr("weighted"),
        )

        with pytest.raises(
            TrustEvaluationError,
            match="no approval store",
        ):
            await service.apply_trust_change(
                NotBlankStr("agent-001"),
                result,
            )

    async def test_returns_none_when_human_approval_with_store(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        """With an approval store, ELEVATED promotion returns None (awaiting)."""
        mock_store = AsyncMock(spec=ApprovalStore)
        strategy = WeightedTrustStrategy(config=weighted_config)
        service = TrustService(
            strategy=strategy,
            config=weighted_config,
            approval_store=mock_store,
        )
        service.initialize_agent(NotBlankStr("agent-001"))

        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.ELEVATED,
            current_level=ToolAccessLevel.STANDARD,
            requires_human_approval=True,
            strategy_name=NotBlankStr("weighted"),
        )

        record = await service.apply_trust_change(
            NotBlankStr("agent-001"),
            result,
        )

        assert record is None
        mock_store.add.assert_awaited_once()
        approval_item = mock_store.add.call_args[0][0]
        assert approval_item.action_type == "trust:promote"
        assert approval_item.metadata["agent_id"] == "agent-001"
        assert approval_item.metadata["recommended_level"] == "elevated"

    async def test_updates_state_after_change(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        strategy = WeightedTrustStrategy(config=weighted_config)
        service = TrustService(
            strategy=strategy,
            config=weighted_config,
        )
        service.initialize_agent(NotBlankStr("agent-001"))

        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.RESTRICTED,
            current_level=ToolAccessLevel.SANDBOXED,
            score=0.6,
            strategy_name=NotBlankStr("weighted"),
        )

        await service.apply_trust_change(
            NotBlankStr("agent-001"),
            result,
        )

        state = service.get_trust_state(NotBlankStr("agent-001"))
        assert state is not None
        assert state.global_level == ToolAccessLevel.RESTRICTED
        assert state.trust_score == 0.6
        assert state.last_promoted_at is not None


# ── Elevated Gate Enforcement (Defense-in-Depth) ─────────────────


@pytest.mark.unit
class TestElevatedGateEnforcement:
    """Tests for defense-in-depth elevated gate in TrustService."""

    def test_elevated_gate_enforces_human_approval(
        self,
        trust_config: TrustConfig,
    ) -> None:
        """If strategy recommends ELEVATED without human approval, override."""
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )
        service.initialize_agent(NotBlankStr("agent-001"))

        # Simulate a result that bypasses human approval
        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.ELEVATED,
            current_level=ToolAccessLevel.STANDARD,
            requires_human_approval=False,
            strategy_name=NotBlankStr("test"),
        )

        enforced = service._enforce_elevated_gate(result)

        assert enforced.requires_human_approval is True
        assert enforced.recommended_level == ToolAccessLevel.ELEVATED

    def test_elevated_gate_no_override_when_already_elevated(
        self,
        trust_config: TrustConfig,
    ) -> None:
        """Agent already at ELEVATED should not trigger the gate."""
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )

        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.ELEVATED,
            current_level=ToolAccessLevel.ELEVATED,
            requires_human_approval=False,
            strategy_name=NotBlankStr("test"),
        )

        enforced = service._enforce_elevated_gate(result)

        assert enforced.requires_human_approval is False

    def test_elevated_gate_no_override_when_approval_set(
        self,
        trust_config: TrustConfig,
    ) -> None:
        """Gate should not override when approval is already required."""
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )

        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.ELEVATED,
            current_level=ToolAccessLevel.STANDARD,
            requires_human_approval=True,
            strategy_name=NotBlankStr("test"),
        )

        enforced = service._enforce_elevated_gate(result)

        assert enforced.requires_human_approval is True


# ── get_trust_state / get_change_history ─────────────────────────


@pytest.mark.unit
class TestStateAndHistory:
    """Tests for trust state retrieval and change history."""

    def test_get_trust_state_unknown_returns_none(
        self,
        trust_config: TrustConfig,
    ) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )

        assert service.get_trust_state(NotBlankStr("nonexistent")) is None

    def test_get_change_history_empty(
        self,
        trust_config: TrustConfig,
    ) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )
        service.initialize_agent(NotBlankStr("agent-001"))

        history = service.get_change_history(NotBlankStr("agent-001"))
        assert history == ()

    async def test_get_change_history_after_change(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        strategy = WeightedTrustStrategy(config=weighted_config)
        service = TrustService(
            strategy=strategy,
            config=weighted_config,
        )
        service.initialize_agent(NotBlankStr("agent-001"))

        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.RESTRICTED,
            current_level=ToolAccessLevel.SANDBOXED,
            score=0.6,
            strategy_name=NotBlankStr("weighted"),
        )
        await service.apply_trust_change(
            NotBlankStr("agent-001"),
            result,
        )

        history = service.get_change_history(NotBlankStr("agent-001"))
        assert len(history) == 1
        assert history[0].old_level == ToolAccessLevel.SANDBOXED
        assert history[0].new_level == ToolAccessLevel.RESTRICTED

    def test_get_change_history_unknown_returns_empty(
        self,
        trust_config: TrustConfig,
    ) -> None:
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )

        history = service.get_change_history(NotBlankStr("nonexistent"))
        assert history == ()


# ── check_decay ──────────────────────────────────────────────────


@pytest.mark.unit
class TestCheckDecay:
    """Tests for TrustService.check_decay."""

    async def test_check_decay_updates_timestamp(
        self,
        trust_config: TrustConfig,
    ) -> None:
        """check_decay updates last_decay_check_at before evaluating."""
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )
        service.initialize_agent(NotBlankStr("agent-001"))
        snapshot = make_performance_snapshot("agent-001")

        result = await service.check_decay(
            NotBlankStr("agent-001"),
            snapshot,
        )

        state = service.get_trust_state(NotBlankStr("agent-001"))
        assert state is not None
        assert state.last_decay_check_at is not None
        assert result.strategy_name == "disabled"

    async def test_check_decay_unknown_agent_raises(
        self,
        trust_config: TrustConfig,
    ) -> None:
        """check_decay raises for unknown agent (via evaluate_agent)."""
        strategy = DisabledTrustStrategy(
            initial_level=trust_config.initial_level,
        )
        service = TrustService(
            strategy=strategy,
            config=trust_config,
        )
        snapshot = make_performance_snapshot("unknown")

        with pytest.raises(TrustEvaluationError, match="not initialized"):
            await service.check_decay(
                NotBlankStr("unknown"),
                snapshot,
            )


# ── apply_trust_change error paths ───────────────────────────────


@pytest.mark.unit
class TestApplyTrustChangeErrorPaths:
    """Tests for apply_trust_change edge cases."""

    async def test_raises_for_uninitialized_agent(
        self,
        weighted_config: TrustConfig,
    ) -> None:
        """apply_trust_change raises for unknown agent."""
        strategy = WeightedTrustStrategy(config=weighted_config)
        service = TrustService(
            strategy=strategy,
            config=weighted_config,
        )

        result = TrustEvaluationResult(
            agent_id=NotBlankStr("unknown"),
            recommended_level=ToolAccessLevel.RESTRICTED,
            current_level=ToolAccessLevel.SANDBOXED,
            strategy_name=NotBlankStr("weighted"),
        )

        with pytest.raises(TrustEvaluationError, match="not initialized"):
            await service.apply_trust_change(
                NotBlankStr("unknown"),
                result,
            )


# ── _infer_reason ────────────────────────────────────────────────


@pytest.mark.unit
class TestInferReason:
    """Tests for TrustService._infer_reason static method."""

    def test_milestone_strategy_returns_milestone_achieved(self) -> None:
        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.RESTRICTED,
            current_level=ToolAccessLevel.SANDBOXED,
            strategy_name=NotBlankStr("milestone"),
        )
        assert (
            TrustService._infer_reason(result) == TrustChangeReason.MILESTONE_ACHIEVED
        )

    def test_weighted_with_score_returns_score_threshold(self) -> None:
        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.RESTRICTED,
            current_level=ToolAccessLevel.SANDBOXED,
            score=0.6,
            strategy_name=NotBlankStr("weighted"),
        )
        assert TrustService._infer_reason(result) == TrustChangeReason.SCORE_THRESHOLD

    def test_per_category_returns_score_threshold(self) -> None:
        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.RESTRICTED,
            current_level=ToolAccessLevel.SANDBOXED,
            strategy_name=NotBlankStr("per_category"),
        )
        assert TrustService._infer_reason(result) == TrustChangeReason.SCORE_THRESHOLD

    def test_unknown_strategy_returns_manual(self) -> None:
        result = TrustEvaluationResult(
            agent_id=NotBlankStr("agent-001"),
            recommended_level=ToolAccessLevel.RESTRICTED,
            current_level=ToolAccessLevel.SANDBOXED,
            strategy_name=NotBlankStr("unknown"),
        )
        assert TrustService._infer_reason(result) == TrustChangeReason.MANUAL
