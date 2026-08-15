"""The capability a piece of work demands, and which agents may take it.

A task's :class:`~synthorg.core.task_enums.Stakes` set a capability floor,
raised one rung by substantial complexity: the weakest rung for low-stakes
work, an expert rung (plus a red-team review mark) for high/critical work.

The requirement decides *which agent* takes the work, never which model runs
behind one agent's name. An agent is a fixed ``(role, personality, model)``
unit, so selection walks a ladder over the roster (an exact match, else the
nearest rung above, else the nearest rung below with the concession logged)
and nothing anywhere rewrites a bound ``(provider, model)`` pair.

There is exactly one :class:`CapabilityPolicy` per process, shared by
selection and by dispatch, so a task cannot be assigned against one verdict
and refused against another. Above the configured park floor a weaker agent
is refused rather than logged, raising
:class:`StakesModelUnavailableError` for the engine to park on the approval
gate or fail loudly.
"""

from synthorg.engine.routing_policy.capability_policy import (
    AgentCapabilityReader,
    CapabilityFit,
    CapabilityPolicy,
    CapabilityVerdict,
    ResolvedAgentCapabilityReader,
    rank_of,
)
from synthorg.engine.routing_policy.config import (
    CapabilityPolicyConfig,
    StakesCapabilityFloor,
    StakesReasoning,
)
from synthorg.engine.routing_policy.errors import StakesModelUnavailableError

__all__ = [
    "AgentCapabilityReader",
    "CapabilityFit",
    "CapabilityPolicy",
    "CapabilityPolicyConfig",
    "CapabilityVerdict",
    "ResolvedAgentCapabilityReader",
    "StakesCapabilityFloor",
    "StakesModelUnavailableError",
    "StakesReasoning",
    "rank_of",
]
