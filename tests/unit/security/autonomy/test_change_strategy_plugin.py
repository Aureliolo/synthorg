"""Tests for the autonomy change-strategy plugin surface (REWORK #9)."""

from typing import cast

import pytest

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.security.autonomy._base_delegate import BaseDelegatingStrategy
from synthorg.security.autonomy.change_strategy import (
    HumanOnlyPromotionStrategy,
)
from synthorg.security.autonomy.change_strategy_config import (
    AutonomyStrategyConfig,
    AutonomyStrategyDeps,
    AutonomyStrategyType,
)
from synthorg.security.autonomy.change_strategy_factory import (
    build_autonomy_change_strategy,
)
from synthorg.security.autonomy.enums import DowngradeReason
from synthorg.security.autonomy.errors import AutonomyStrategyConfigError
from synthorg.security.autonomy.protocol import AutonomyChangeStrategy

pytestmark = pytest.mark.unit

_AGENT = "agent-1"


class _FixedPerf:
    def __init__(self, rate: float | None) -> None:
        self._rate = rate

    def success_rate(self, agent_id: str) -> float | None:
        return self._rate


class _FixedBudget:
    def __init__(self, headroom: float) -> None:
        self._headroom = headroom

    def headroom_fraction(self) -> float:
        return self._headroom


class TestHumanOnlyDefault:
    def test_default_is_bare_human_only(self) -> None:
        strategy = build_autonomy_change_strategy(
            AutonomyStrategyConfig(),
            AutonomyStrategyDeps(),
        )
        assert isinstance(strategy, HumanOnlyPromotionStrategy)
        assert isinstance(strategy, AutonomyChangeStrategy)
        assert strategy.request_promotion(_AGENT, AutonomyLevel.FULL) is False
        assert strategy.request_recovery(_AGENT) is False


class TestPerformanceGated:
    def test_grants_above_threshold(self) -> None:
        strategy = build_autonomy_change_strategy(
            AutonomyStrategyConfig(
                kind=AutonomyStrategyType.PERFORMANCE_GATED,
                promotion_success_threshold=0.8,
            ),
            AutonomyStrategyDeps(performance_signal=_FixedPerf(0.95)),
        )
        assert strategy.request_promotion(_AGENT, AutonomyLevel.FULL) is True

    def test_defers_below_threshold(self) -> None:
        strategy = build_autonomy_change_strategy(
            AutonomyStrategyConfig(
                kind=AutonomyStrategyType.PERFORMANCE_GATED,
                promotion_success_threshold=0.8,
            ),
            AutonomyStrategyDeps(performance_signal=_FixedPerf(0.5)),
        )
        assert strategy.request_promotion(_AGENT, AutonomyLevel.FULL) is False

    def test_none_history_defers(self) -> None:
        strategy = build_autonomy_change_strategy(
            AutonomyStrategyConfig(
                kind=AutonomyStrategyType.PERFORMANCE_GATED,
            ),
            AutonomyStrategyDeps(performance_signal=_FixedPerf(None)),
        )
        assert strategy.request_promotion(_AGENT, AutonomyLevel.FULL) is False

    def test_missing_signal_raises(self) -> None:
        with pytest.raises(AutonomyStrategyConfigError, match="performance_signal"):
            build_autonomy_change_strategy(
                AutonomyStrategyConfig(
                    kind=AutonomyStrategyType.PERFORMANCE_GATED,
                ),
                AutonomyStrategyDeps(),
            )

    def test_downgrade_delegates_to_base(self) -> None:
        strategy = build_autonomy_change_strategy(
            AutonomyStrategyConfig(
                kind=AutonomyStrategyType.PERFORMANCE_GATED,
            ),
            AutonomyStrategyDeps(performance_signal=_FixedPerf(0.99)),
        )
        level = strategy.auto_downgrade(
            _AGENT,
            DowngradeReason.SECURITY_INCIDENT,
            AutonomyLevel.FULL,
        )
        assert level is AutonomyLevel.LOCKED


class TestBudgetAware:
    def test_denies_under_budget_stress(self) -> None:
        strategy = build_autonomy_change_strategy(
            AutonomyStrategyConfig(
                kind=AutonomyStrategyType.BUDGET_AWARE,
                budget_warn_fraction=0.3,
            ),
            AutonomyStrategyDeps(risk_budget_signal=_FixedBudget(0.1)),
        )
        assert strategy.request_promotion(_AGENT, AutonomyLevel.FULL) is False

    def test_delegates_when_headroom_ok(self) -> None:
        # Headroom OK -> delegates to base HumanOnly -> still False,
        # but via the base path (not the budget short-circuit).
        strategy = build_autonomy_change_strategy(
            AutonomyStrategyConfig(
                kind=AutonomyStrategyType.BUDGET_AWARE,
                budget_warn_fraction=0.3,
            ),
            AutonomyStrategyDeps(risk_budget_signal=_FixedBudget(0.9)),
        )
        assert strategy.request_promotion(_AGENT, AutonomyLevel.FULL) is False

    def test_missing_signal_raises(self) -> None:
        with pytest.raises(AutonomyStrategyConfigError, match="risk_budget_signal"):
            build_autonomy_change_strategy(
                AutonomyStrategyConfig(kind=AutonomyStrategyType.BUDGET_AWARE),
                AutonomyStrategyDeps(),
            )


class TestEscalationChain:
    def test_always_pending(self) -> None:
        strategy = build_autonomy_change_strategy(
            AutonomyStrategyConfig(
                kind=AutonomyStrategyType.ESCALATION_CHAIN,
                escalation_chain=("supervisor", "manager", "ceo"),
            ),
            AutonomyStrategyDeps(),
        )
        assert strategy.request_promotion(_AGENT, AutonomyLevel.FULL) is False

    def test_recovery_and_override_delegate(self) -> None:
        base = HumanOnlyPromotionStrategy()
        strategy = build_autonomy_change_strategy(
            AutonomyStrategyConfig(
                kind=AutonomyStrategyType.ESCALATION_CHAIN,
            ),
            AutonomyStrategyDeps(base=base),
        )
        # Downgrade via the wrapper records the override on the base.
        strategy.auto_downgrade(
            _AGENT,
            DowngradeReason.HIGH_ERROR_RATE,
            AutonomyLevel.FULL,
        )
        assert base.get_override(_AGENT) is not None
        # The wrapper exposes the delegated override-store ops too.
        wrapper = cast("BaseDelegatingStrategy", strategy)
        assert wrapper.get_override(_AGENT) is not None
        assert wrapper.clear_override(_AGENT) is True


class TestFactoryErrors:
    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(StrategyFactoryNotFoundError):
            build_autonomy_change_strategy(
                AutonomyStrategyConfig.model_construct(kind="bogus"),  # type: ignore[arg-type]
                AutonomyStrategyDeps(),
            )
