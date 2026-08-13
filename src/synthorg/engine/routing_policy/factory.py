"""Factory for building a stakes router from config.

Dispatches on ``StakesRoutingConfig.strategy`` via a ``StrategyRegistry``
(mirrors ``loop_selector._LOOP_REGISTRY``). The ``stakes_aware`` strategy
requires a capability-floor policy (to gate the bound agent against the rung
its task demands); ``flat`` needs no dependencies.
"""

from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.core.registry import StrategyRegistry
from synthorg.engine.routing_policy.capability_floor import CapabilityFloorPolicy
from synthorg.engine.routing_policy.config import StakesRoutingConfig
from synthorg.engine.routing_policy.protocol import StakesRoutingStrategy
from synthorg.engine.routing_policy.router import StakesRouter
from synthorg.engine.routing_policy.strategies import FlatStrategy, StakesAwareStrategy


def _build_flat(
    *,
    config: StakesRoutingConfig,
    floor_policy: CapabilityFloorPolicy | None = None,
    coordination_store: CoordinationMetricsStore | None = None,
) -> StakesRoutingStrategy:
    del config, floor_policy, coordination_store
    return FlatStrategy()


def _build_stakes_aware(
    *,
    config: StakesRoutingConfig,
    floor_policy: CapabilityFloorPolicy | None = None,
    coordination_store: CoordinationMetricsStore | None = None,
) -> StakesRoutingStrategy:
    if floor_policy is None:
        msg = "stakes_aware routing requires a capability-floor policy"
        raise ValueError(msg)
    return StakesAwareStrategy(
        floor_policy=floor_policy,
        config=config,
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
    floor_policy: CapabilityFloorPolicy | None = None,
    coordination_store: CoordinationMetricsStore | None = None,
) -> StakesRouter:
    """Build a :class:`StakesRouter` from *config*.

    Args:
        config: Routing config; defaults to the ``stakes_aware`` strategy.
        floor_policy: Stakes-to-rung floor plus the agent-rung reader
            (required for ``stakes_aware``).
        coordination_store: Recent coordination metrics for the nudge.

    Returns:
        A configured stakes router.

    Raises:
        StrategyFactoryNotFoundError: If ``config.strategy`` is unknown.
        ValueError: If ``stakes_aware`` is selected without a floor policy.
    """
    cfg = config or StakesRoutingConfig()
    strategy = _STRATEGY_REGISTRY.build(
        cfg.strategy,
        config=cfg,
        floor_policy=floor_policy,
        coordination_store=coordination_store,
    )
    return StakesRouter(strategy)
