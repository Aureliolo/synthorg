"""Tests for the autonomy change-strategy plugin surface."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from structlog.testing import capture_logs

from synthorg.api.app_builders import _build_configured_autonomy_change_strategy
from synthorg.budget.risk_config import RiskBudgetConfig
from synthorg.budget.risk_record import RiskRecord
from synthorg.budget.risk_tracker import RiskTracker
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_PROMOTION_DENIED,
)
from synthorg.security.autonomy._base_delegate import BaseDelegatingStrategy
from synthorg.security.autonomy.budget_aware import BudgetAwarePromotionStrategy
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
from synthorg.security.autonomy.errors import AutonomyStrategyConfigError
from synthorg.security.autonomy.models import AutonomyConfig
from synthorg.security.autonomy.protocol import AutonomyChangeStrategy
from synthorg.security.risk_scorer import RiskScore

pytestmark = pytest.mark.unit

_AGENT = "agent-1"
_HUMAN_APPROVAL_DENIAL = "human approval required"
_RISK_BUDGET_DENIAL = "risk-budget headroom below warn fraction"


def _denial_reasons(entries: Sequence[Mapping[str, Any]]) -> set[str]:  # type: ignore[explicit-any]  # structlog capture yields untyped event dicts
    """Collect the reasons carried on captured promotion denials.

    Returns:
        Every distinct ``reason`` on a promotion-denied event.
    """
    return {
        str(entry["reason"])
        for entry in entries
        if entry.get("event") == SECURITY_AUTONOMY_PROMOTION_DENIED
    }


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

    def test_the_wrapper_holds_the_base_it_was_given(self) -> None:
        """The one thing a wrapper still delegates is the promotion decision.

        Asserted on the held base rather than through a call, because this
        chain strategy answers pending itself and never reaches the base;
        ``TestBudgetAware`` covers the wrapper that does.
        """
        base = HumanOnlyPromotionStrategy()
        strategy = build_autonomy_change_strategy(
            AutonomyStrategyConfig(
                kind=AutonomyStrategyType.ESCALATION_CHAIN,
            ),
            AutonomyStrategyDeps(base=base),
        )

        wrapper = cast("BaseDelegatingStrategy", strategy)

        assert wrapper._base is base


class TestBootSeamSuppliesTheSignal:
    """Selecting ``BUDGET_AWARE`` must be satisfiable, not just declarable.

    Without a shipped provider the only outcome of choosing that kind was the
    construction error above, so the option had no reachable path to being
    honoured. These pin the boot builder handing it a real ``RiskTracker``.
    """

    def test_budget_aware_comes_up_through_the_shipped_builder(self) -> None:
        strategy = _build_configured_autonomy_change_strategy(
            AutonomyConfig(
                change_strategy=AutonomyStrategyConfig(
                    kind=AutonomyStrategyType.BUDGET_AWARE,
                    budget_warn_fraction=0.3,
                ),
            ),
            risk_budget_signal=RiskTracker(),
        )

        assert isinstance(strategy, BudgetAwarePromotionStrategy)

    async def test_the_signal_reads_the_ledger_the_org_records_into(self) -> None:
        """The ledger has to be what denies, not the delegate beneath it.

        ``HumanOnlyPromotionStrategy`` denies every promotion on its own, so
        a bare ``is False`` holds whether or not the signal was ever
        consulted. The reason carried on the denial is what separates a
        strategy reading the ledger from one inheriting a refusal.
        """
        tracker = RiskTracker(
            risk_budget_config=RiskBudgetConfig(
                enabled=True,
                per_task_risk_limit=1.0,
                per_agent_daily_risk_limit=1.0,
                total_daily_risk_limit=1.0,
            ),
        )
        strategy = _build_configured_autonomy_change_strategy(
            AutonomyConfig(
                change_strategy=AutonomyStrategyConfig(
                    kind=AutonomyStrategyType.BUDGET_AWARE,
                    budget_warn_fraction=0.5,
                ),
            ),
            risk_budget_signal=tracker,
        )

        with capture_logs() as untouched_budget:
            assert strategy.request_promotion(_AGENT, AutonomyLevel.FULL) is False
        assert _denial_reasons(untouched_budget) == {_HUMAN_APPROVAL_DENIAL}

        await tracker.record(
            RiskRecord(
                agent_id=_AGENT,
                task_id="task-1",
                action_type="code:write",
                risk_score=RiskScore(
                    reversibility=0.9,
                    blast_radius=0.9,
                    data_sensitivity=0.9,
                    external_visibility=0.9,
                ),
                risk_units=0.8,
                # Stamped live: the tracker measures its daily window against
                # wall-clock ``now`` with no clock seam to inject, so any fixed
                # instant ages out of the window and stops stressing the budget.
                timestamp=datetime.now(UTC),
            ),
        )

        with capture_logs() as stressed_budget:
            assert strategy.request_promotion(_AGENT, AutonomyLevel.FULL) is False
        # The delegate is never reached now, so its reason is gone: the
        # ledger entry is what decided.
        assert _denial_reasons(stressed_budget) == {_RISK_BUDGET_DENIAL}


class TestFactoryErrors:
    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(StrategyFactoryNotFoundError):
            build_autonomy_change_strategy(
                AutonomyStrategyConfig.model_construct(kind="bogus"),  # type: ignore[arg-type]
                AutonomyStrategyDeps(),
            )
