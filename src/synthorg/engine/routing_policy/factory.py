"""Factory for building a stakes router from config.

Dispatches on ``StakesRoutingConfig.strategy`` via a ``StrategyRegistry``
(mirrors ``loop_selector._LOOP_REGISTRY``). The ``stakes_aware`` strategy
requires a benchmark provider; ``flat`` needs no dependencies.
"""

from typing import TYPE_CHECKING

from synthorg.core.registry import StrategyRegistry
from synthorg.engine.routing_policy.config import StakesRoutingConfig
from synthorg.engine.routing_policy.protocol import (  # noqa: TC001 -- registry generic + annotations
    StakesRoutingStrategy,
)
from synthorg.engine.routing_policy.router import StakesRouter
from synthorg.engine.routing_policy.strategies import FlatStrategy, StakesAwareStrategy

if TYPE_CHECKING:
    from synthorg.budget.benchmark_protocol import BenchmarkScoreProvider
    from synthorg.budget.coordination_store import CoordinationMetricsStore
    from synthorg.providers.routing.resolver import ModelResolver


def _build_flat(
    *,
    config: StakesRoutingConfig,
    benchmark_provider: BenchmarkScoreProvider | None = None,
    resolver: ModelResolver | None = None,
    coordination_store: CoordinationMetricsStore | None = None,
) -> StakesRoutingStrategy:
    del config, benchmark_provider, resolver, coordination_store
    return FlatStrategy()


def _build_stakes_aware(
    *,
    config: StakesRoutingConfig,
    benchmark_provider: BenchmarkScoreProvider | None = None,
    resolver: ModelResolver | None = None,
    coordination_store: CoordinationMetricsStore | None = None,
) -> StakesRoutingStrategy:
    if benchmark_provider is None:
        msg = "stakes_aware routing requires a benchmark score provider"
        raise ValueError(msg)
    return StakesAwareStrategy(
        benchmark_provider=benchmark_provider,
        config=config,
        resolver=resolver,
        coordination_store=coordination_store,
    )


_STRATEGY_REGISTRY: StrategyRegistry[StakesRoutingStrategy] = StrategyRegistry(
    {
        "stakes_aware": _build_stakes_aware,
        "flat": _build_flat,
    },
    kind="stakes_routing_strategy",
)


def build_stakes_router(
    config: StakesRoutingConfig | None = None,
    *,
    benchmark_provider: BenchmarkScoreProvider | None = None,
    resolver: ModelResolver | None = None,
    coordination_store: CoordinationMetricsStore | None = None,
) -> StakesRouter:
    """Build a :class:`StakesRouter` from *config*.

    Args:
        config: Routing config; defaults to the ``stakes_aware`` strategy.
        benchmark_provider: Per-model quality scores (required for
            ``stakes_aware``).
        resolver: Tier-to-model resolver. When absent, ``stakes_aware``
            applies only the red-team mark.
        coordination_store: Recent coordination metrics for the nudge.

    Returns:
        A configured stakes router.

    Raises:
        StrategyFactoryNotFoundError: If ``config.strategy`` is unknown.
        ValueError: If ``stakes_aware`` is selected without a benchmark
            provider.
    """
    cfg = config or StakesRoutingConfig()
    strategy = _STRATEGY_REGISTRY.build(
        cfg.strategy,
        config=cfg,
        benchmark_provider=benchmark_provider,
        resolver=resolver,
        coordination_store=coordination_store,
    )
    return StakesRouter(strategy)
