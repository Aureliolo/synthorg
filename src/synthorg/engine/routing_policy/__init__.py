"""Stakes-aware model routing.

Given a task's :class:`~synthorg.core.task_enums.Stakes` and an agent's
configured model, the routing layer picks the cheapest configured,
tool-capable model whose assigned tier meets the per-stakes tier
requirement: the cheapest tier for low-stakes work, and a strong tier
(plus a red-team review mark) for high/critical-stakes work. Coordination
metrics nudge the choice upward when recent runs show error amplification
or overhead. When no configured tool-capable model meets the requirement,
the strategy raises :class:`StakesModelUnavailableError` so the engine
escalates or fails loudly rather than silently running a sub-tier model.

The decision is a pure function of the task, the injected model
:class:`~synthorg.providers.routing.resolver.ModelResolver` catalogue,
recent :class:`~synthorg.budget.coordination_store.CoordinationMetricsStore`
records, the agent identity, and the configured tier requirements. It
composes with (runs before) the existing budget auto-downgrade, which may
lower the tier further when budget is tight.
"""

from synthorg.engine.routing_policy.config import (
    StakesRoutingConfig,
    StakesTierRequirement,
)
from synthorg.engine.routing_policy.errors import StakesModelUnavailableError
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
    "StakesAwareStrategy",
    "StakesModelUnavailableError",
    "StakesRouter",
    "StakesRoutingConfig",
    "StakesRoutingDecision",
    "StakesRoutingStrategy",
    "StakesTierRequirement",
    "build_stakes_router",
]
