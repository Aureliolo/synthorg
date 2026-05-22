"""Stakes-aware model routing.

Given a task's :class:`~synthorg.core.enums.Stakes` and an agent's
configured model, the routing layer picks a model tier matched to the
stakes: the cheapest tier that clears a benchmark-derived quality floor
for low/normal-stakes work, and a strong tier (plus a red-team review
mark) for high/critical-stakes work. Coordination metrics nudge the
choice upward when recent runs show error amplification or overhead.

The decision is a pure function of the task, the injected
:class:`~synthorg.budget.benchmark_protocol.BenchmarkScoreProvider`
scores, recent
:class:`~synthorg.budget.coordination_store.CoordinationMetricsStore`
records, the agent identity, and the configured floors. It composes
with (runs before) the existing budget auto-downgrade, which may lower
the tier further when budget is tight.
"""

from synthorg.engine.routing_policy.config import (
    QualityFloors,
    StakesRoutingConfig,
)
from synthorg.engine.routing_policy.factory import build_stakes_router
from synthorg.engine.routing_policy.models import StakesRoutingDecision
from synthorg.engine.routing_policy.protocol import StakesRoutingStrategy
from synthorg.engine.routing_policy.router import StakesRouter
from synthorg.engine.routing_policy.strategies import (
    FlatStrategy,
    StakesAwareStrategy,
)

__all__ = [
    "FlatStrategy",
    "QualityFloors",
    "StakesAwareStrategy",
    "StakesRouter",
    "StakesRoutingConfig",
    "StakesRoutingDecision",
    "StakesRoutingStrategy",
    "build_stakes_router",
]
