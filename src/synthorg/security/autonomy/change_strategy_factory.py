"""Autonomy change-strategy factory.

Maps :class:`AutonomyStrategyType` to a concrete
:class:`AutonomyChangeStrategy` via the ``StrEnum``-keyed
:class:`~synthorg.core.registry.StrategyRegistry`. ``HUMAN_ONLY``
resolves to a :class:`HumanOnlyPromotionStrategy` -- ``deps.base``
when it is already one (so its override store is preserved),
otherwise a fresh instance; the wrapping strategies require a signal
provider and raise :class:`AutonomyStrategyConfigError` when it is
absent (fail fast at construction).
"""

from typing import TYPE_CHECKING

from synthorg.core.registry import StrategyRegistry
from synthorg.security.autonomy.budget_aware import (
    BudgetAwarePromotionStrategy,
)
from synthorg.security.autonomy.change_strategy import (
    HumanOnlyPromotionStrategy,
)
from synthorg.security.autonomy.change_strategy_config import (
    AutonomyStrategyConfig,
    AutonomyStrategyDeps,
    AutonomyStrategyType,
)
from synthorg.security.autonomy.errors import AutonomyStrategyConfigError
from synthorg.security.autonomy.escalation_chain import (
    EscalationChainPromotionStrategy,
)
from synthorg.security.autonomy.performance_gated import (
    PerformanceGatedPromotionStrategy,
)

if TYPE_CHECKING:
    from synthorg.security.autonomy.protocol import AutonomyChangeStrategy


def _base(deps: AutonomyStrategyDeps) -> HumanOnlyPromotionStrategy:
    """Return the override-store-bearing base for the wrappers.

    The wrappers delegate downgrade / recovery / override-store ops
    to a :class:`HumanOnlyPromotionStrategy`. ``deps.base`` is honoured
    only when it is one (the override store lives there); otherwise a
    fresh instance is created.
    """
    if isinstance(deps.base, HumanOnlyPromotionStrategy):
        return deps.base
    return HumanOnlyPromotionStrategy()


def _build_human_only(
    _config: AutonomyStrategyConfig,
    deps: AutonomyStrategyDeps,
) -> AutonomyChangeStrategy:
    return _base(deps)


def _build_performance_gated(
    config: AutonomyStrategyConfig,
    deps: AutonomyStrategyDeps,
) -> AutonomyChangeStrategy:
    if deps.performance_signal is None:
        msg = (
            "PERFORMANCE_GATED autonomy strategy requires a "
            "'performance_signal' dependency but none was provided"
        )
        raise AutonomyStrategyConfigError(msg)
    return PerformanceGatedPromotionStrategy(
        base=_base(deps),
        performance_signal=deps.performance_signal,
        success_threshold=config.promotion_success_threshold,
    )


def _build_budget_aware(
    config: AutonomyStrategyConfig,
    deps: AutonomyStrategyDeps,
) -> AutonomyChangeStrategy:
    if deps.risk_budget_signal is None:
        msg = (
            "BUDGET_AWARE autonomy strategy requires a "
            "'risk_budget_signal' dependency but none was provided"
        )
        raise AutonomyStrategyConfigError(msg)
    return BudgetAwarePromotionStrategy(
        base=_base(deps),
        risk_budget_signal=deps.risk_budget_signal,
        warn_fraction=config.budget_warn_fraction,
    )


def _build_escalation_chain(
    config: AutonomyStrategyConfig,
    deps: AutonomyStrategyDeps,
) -> AutonomyChangeStrategy:
    return EscalationChainPromotionStrategy(
        base=_base(deps),
        chain=config.escalation_chain,
    )


_REGISTRY: StrategyRegistry[AutonomyChangeStrategy] = StrategyRegistry(
    {
        AutonomyStrategyType.HUMAN_ONLY: _build_human_only,
        AutonomyStrategyType.PERFORMANCE_GATED: _build_performance_gated,
        AutonomyStrategyType.BUDGET_AWARE: _build_budget_aware,
        AutonomyStrategyType.ESCALATION_CHAIN: _build_escalation_chain,
    },
    kind="autonomy_change_strategy",
)


def build_autonomy_change_strategy(
    config: AutonomyStrategyConfig,
    deps: AutonomyStrategyDeps,
) -> AutonomyChangeStrategy:
    """Build the configured :class:`AutonomyChangeStrategy`.

    Args:
        config: The strategy discriminator + per-impl tuning.
        deps: Runtime collaborators (base, signal providers).

    Returns:
        A strategy satisfying the ``AutonomyChangeStrategy`` protocol.
        ``config.kind == HUMAN_ONLY`` yields a
        ``HumanOnlyPromotionStrategy`` (``deps.base`` when already one,
        else a fresh instance) -- behaviour identical either way.

    Raises:
        StrategyFactoryNotFoundError: Unknown ``config.kind``.
        AutonomyStrategyConfigError: A wrapping strategy is missing a
            required signal provider.
    """
    return _REGISTRY.build(config.kind, config, deps)
