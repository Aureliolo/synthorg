"""Stakes-aware capability gating.

A task's :class:`~synthorg.core.task_enums.Stakes` set a capability floor:
the weakest rung for low-stakes work, and an expert rung (plus a red-team
review mark) for high/critical-stakes work. Coordination metrics nudge the
floor up a rung when recent runs show error amplification or overhead.

The floor decides *which agent* may take the work, never which model runs
behind one agent's name. An agent is a fixed ``(role, personality, model)``
unit, so the assignment layer filters the roster on the same floor this
package computes, and a bound agent that does not clear it raises
:class:`StakesModelUnavailableError` for the engine to park or fail loudly.
Consequential work is neither quietly upgraded onto a model the agent is
not, nor quietly run under-capable.

The decision is a pure function of the task, the injected
:class:`~synthorg.engine.routing_policy.capability_floor.CapabilityFloorPolicy`,
recent :class:`~synthorg.budget.coordination_store.CoordinationMetricsStore`
records, and the agent identity. It runs before the budget auto-downgrade,
which is a separate mechanism and NOT an exception to the rule above: an
operator-configured cost ceiling may still lower the model a run executes
on. The difference is who decided and whether anyone is told. Stakes routing
is the loop choosing horsepower on its own, which is what this package
refuses; the ceiling is an operator's own instruction, and when it fires
after the gate has passed the agent it logs
``STAKES_ROUTING_BUDGET_OVERRODE`` saying so.
"""

from synthorg.engine.routing_policy.capability_floor import (
    AgentCapabilityReader,
    CapabilityFloorPolicy,
    ResolvedAgentCapabilityReader,
    clears_floor,
)
from synthorg.engine.routing_policy.config import (
    StakesCapabilityFloor,
    StakesRoutingConfig,
)
from synthorg.engine.routing_policy.errors import (
    StakesModelUnavailableError,
    StakesRoutingConfigError,
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
    "AgentCapabilityReader",
    "CapabilityFloorPolicy",
    "FlatStrategy",
    "ResolvedAgentCapabilityReader",
    "StakesAwareStrategy",
    "StakesCapabilityFloor",
    "StakesModelUnavailableError",
    "StakesRouter",
    "StakesRoutingConfig",
    "StakesRoutingConfigError",
    "StakesRoutingDecision",
    "StakesRoutingStrategy",
    "build_stakes_router",
    "clears_floor",
]
