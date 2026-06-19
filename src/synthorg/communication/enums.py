"""Communication domain enumerations."""

from enum import StrEnum


class MessageType(StrEnum):
    """Type of inter-agent message.

    Maps to the ``type`` field in the Communication design page.
    """

    TASK_UPDATE = "task_update"
    QUESTION = "question"
    ANNOUNCEMENT = "announcement"
    REVIEW_REQUEST = "review_request"
    APPROVAL = "approval"
    DELEGATION = "delegation"
    STATUS_REPORT = "status_report"
    ESCALATION = "escalation"
    MEETING_CONTRIBUTION = "meeting_contribution"
    HR_NOTIFICATION = "hr_notification"
    DISSENT = "dissent"
    CONTEXT_INJECTION = "context_injection"


class MessagePriority(StrEnum):
    """Priority level for messages.

    Separate from :class:`synthorg.core.task_enums.Priority` which uses
    ``"medium"``; message priority uses ``"normal"`` per the Communication design page.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class ChannelType(StrEnum):
    """Channel delivery semantics.

    Members:
        TOPIC: Publish-subscribe delivery to all subscribers.
        DIRECT: Point-to-point delivery to a single recipient.
        BROADCAST: Delivery to all agents regardless of subscription.
    """

    TOPIC = "topic"
    DIRECT = "direct"
    BROADCAST = "broadcast"


class CommunicationPattern(StrEnum):
    """High-level communication pattern for the company.

    Maps to the Communication design page.
    """

    EVENT_DRIVEN = "event_driven"
    HIERARCHICAL = "hierarchical"
    MEETING_BASED = "meeting_based"
    HYBRID = "hybrid"


class ConflictType(StrEnum):
    """Type of inter-agent conflict (see Communication design page).

    Members:
        ARCHITECTURE: Disagreement on system design choices.
        IMPLEMENTATION: Disagreement on implementation approach.
        PRIORITY: Disagreement on task priority or ordering.
        RESOURCE: Disagreement on resource allocation.
        PROCESS: Disagreement on process or methodology.
        OTHER: Any other type of conflict.
    """

    ARCHITECTURE = "architecture"
    IMPLEMENTATION = "implementation"
    PRIORITY = "priority"
    RESOURCE = "resource"
    PROCESS = "process"
    OTHER = "other"


class ConflictResolutionStrategy(StrEnum):
    """Strategy for resolving inter-agent conflicts (see Communication design page).

    Members:
        AUTHORITY: Resolve by seniority/hierarchy with dissent log.
        DEBATE: Structured debate with judge evaluation.
        HUMAN: Escalate to human for resolution.
        HYBRID: Combination of automated review and escalation.
    """

    AUTHORITY = "authority"
    DEBATE = "debate"
    HUMAN = "human"
    HYBRID = "hybrid"


class QuadraticEnforcementStrategy(StrEnum):
    """Enforcement mode for O(n^2) message-overhead detection.

    Detection (``MessageOverhead.is_quadratic``) always computes the
    same signal; the strategy decides what the message bus does once a
    coordination crosses the quadratic threshold.

    Members:
        DISABLED: No detection and no enforcement (zero hot-path cost).
        ALERT_ONLY: Detect and emit a warning event / notification; the
            default, behaviour-preserving for message delivery.
        SOFT_THROTTLE: Alert, then apply publish backpressure (a per-
            publish delay) to the over-communicating bus so the message
            rate is automatically dampened.
        HARD_BLOCK: Alert, then reject new agent connections once the
            live participant count exceeds ``max_agent_connections``.
    """

    DISABLED = "disabled"
    ALERT_ONLY = "alert_only"
    SOFT_THROTTLE = "soft_throttle"
    HARD_BLOCK = "hard_block"


class MessageBusBackend(StrEnum):
    """Message bus backend implementation.

    Maps to the Communication design page ``message_bus.backend``.
    ``INTERNAL`` (single-process asyncio queues) and ``NATS`` (JetStream
    for distributed deployments) are the only shipped backends.  The
    alternatives evaluated during the original backend-selection work
    live in ``docs/design/distributed-runtime.md``.
    """

    INTERNAL = "internal"
    NATS = "nats"
