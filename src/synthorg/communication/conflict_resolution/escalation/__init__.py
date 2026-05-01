"""Human escalation approval queue.

Pluggable backend for the :class:`HumanEscalationResolver` strategy:
persistent queue of pending escalations, operator-facing REST decision
endpoint, and dispatch-back of human decisions into the resolver.

Follows the Protocol + strategy + factory + config discriminator shape
prescribed by ``CLAUDE.md`` for cross-cutting subsystems.
"""

from synthorg.communication.conflict_resolution.escalation.config import (
    EscalationQueueConfig,
)
from synthorg.communication.conflict_resolution.escalation.factory import (
    build_decision_processor,
    build_escalation_notify_subscriber,
    build_escalation_queue_store,
)
from synthorg.communication.conflict_resolution.escalation.in_memory_store import (
    InMemoryEscalationStore,
)
from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationDecision,
    EscalationStatus,
    RejectDecision,
    WinnerDecision,
)
from synthorg.communication.conflict_resolution.escalation.notify import (
    EscalationNotifySubscriber,
    NoopEscalationNotifySubscriber,
)
from synthorg.communication.conflict_resolution.escalation.processors import (
    HybridDecisionProcessor,
    WinnerSelectProcessor,
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    DecisionProcessor,
    EscalationQueueStore,
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)
from synthorg.communication.conflict_resolution.escalation.sweeper import (
    EscalationExpirationSweeper,
)

__all__ = [
    "DecisionProcessor",
    "Escalation",
    "EscalationDecision",
    "EscalationExpirationSweeper",
    "EscalationNotifySubscriber",
    "EscalationQueueConfig",
    "EscalationQueueStore",
    "EscalationStatus",
    "HybridDecisionProcessor",
    "InMemoryEscalationStore",
    "NoopEscalationNotifySubscriber",
    "PendingFuturesRegistry",
    "RejectDecision",
    "WinnerDecision",
    "WinnerSelectProcessor",
    "build_decision_processor",
    "build_escalation_notify_subscriber",
    "build_escalation_queue_store",
]
