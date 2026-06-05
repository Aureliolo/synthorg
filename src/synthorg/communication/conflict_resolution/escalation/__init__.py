"""Human escalation approval queue.

Pluggable backend for the :class:`HumanEscalationResolver` strategy:
persistent queue of pending escalations, operator-facing REST decision
endpoint, and dispatch-back of human decisions into the resolver.

Follows the Protocol + strategy + factory + config discriminator shape
prescribed by ``CLAUDE.md`` for cross-cutting subsystems.

Only the lightweight config / models / protocol abstractions are
re-exported here. The concrete store, factory, processors, notify
subscriber, registry, and sweeper are imported directly from their
defining submodules so that merely importing ``escalation.config``
(reached transitively from ``communication.config``) does not pull the
``persistence`` package onto the config-load path and re-introduce a
cold-import cycle.
"""

from synthorg.communication.conflict_resolution.escalation.config import (
    EscalationQueueConfig,
)
from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationDecision,
    EscalationStatus,
    RejectDecision,
    WinnerDecision,
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    DecisionProcessor,
    EscalationQueueStore,
)

__all__ = [
    "DecisionProcessor",
    "Escalation",
    "EscalationDecision",
    "EscalationQueueConfig",
    "EscalationQueueStore",
    "EscalationStatus",
    "RejectDecision",
    "WinnerDecision",
]
