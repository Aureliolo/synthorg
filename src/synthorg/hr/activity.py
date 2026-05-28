"""Activity timeline models and pure functions for building agent timelines.

Merges lifecycle events, task metrics, cost records, tool invocations,
and delegation records into a unified chronological timeline, and
filters career-relevant events.
"""

import re
from typing import TYPE_CHECKING

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.budget.currency import DEFAULT_CURRENCY, format_cost_detail
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import ActivityEventType, LifecycleEventType
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_ACTIVITY_REDACTION_MISMATCH

logger = get_logger(__name__)

if TYPE_CHECKING:
    from synthorg.budget.cost_record import CostRecord
    from synthorg.communication.delegation.models import DelegationRecord
    from synthorg.hr.models import AgentLifecycleEvent
    from synthorg.hr.performance.models import TaskMetricRecord
    from synthorg.tools.invocation_record import ToolInvocationRecord


class ActivityEvent(BaseModel):
    """Single event in an agent's activity timeline.

    Attributes:
        event_type: Event category (e.g. ``"hired"``, ``"task_completed"``).
        timestamp: When the event occurred.
        description: Human-readable event description.
        related_ids: Related entity identifiers (e.g. task_id, agent_id).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    event_type: ActivityEventType = Field(description="Event category")
    timestamp: AwareDatetime = Field(description="When the event occurred")
    description: str = Field(
        default="",
        max_length=1024,
        description="Human-readable event description",
    )
    related_ids: dict[str, str] = Field(
        default_factory=dict,
        description="Related entity identifiers",
    )


class CareerEvent(BaseModel):
    """Career milestone in an agent's history.

    Attributes:
        event_type: Lifecycle event type (e.g. ``"hired"``, ``"promoted"``).
        timestamp: When the event occurred.
        description: Human-readable event description.
        initiated_by: Who triggered the event.
        metadata: Additional structured metadata.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    event_type: LifecycleEventType = Field(description="Lifecycle event type")
    timestamp: AwareDatetime = Field(description="When the event occurred")
    description: str = Field(
        default="",
        max_length=1024,
        description="Human-readable event description",
    )
    initiated_by: NotBlankStr = Field(description="Who triggered the event")
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Additional structured metadata",
    )


_CAREER_EVENT_TYPES: frozenset[LifecycleEventType] = frozenset(
    {
        LifecycleEventType.HIRED,
        LifecycleEventType.FIRED,
        LifecycleEventType.PROMOTED,
        LifecycleEventType.DEMOTED,
        LifecycleEventType.ONBOARDED,
    }
)


# ── Converter functions ──────────────────────────────────────────


def _lifecycle_to_activity(event: AgentLifecycleEvent) -> ActivityEvent:
    """Convert a lifecycle event to a timeline activity event.

    Returns:
        Result of type ``ActivityEvent``.
    """
    activity_type = ActivityEventType(event.event_type.value)
    return ActivityEvent(
        event_type=activity_type,
        timestamp=event.timestamp,
        description=event.details or f"Agent {activity_type.value}",
        related_ids={"agent_id": str(event.agent_id)},
    )


def _task_metric_to_activity(
    record: TaskMetricRecord,
    *,
    currency: str = DEFAULT_CURRENCY,
) -> ActivityEvent:
    """Convert a task metric record to a task_completed event (success or failure).

    Returns:
        Result of type ``ActivityEvent``.
    """
    status = "succeeded" if record.is_success else "failed"
    desc = (
        f"Task {record.task_id} {status} "
        f"({record.duration_seconds:.1f}s, "
        f"{format_cost_detail(record.cost, currency)})"
    )
    return ActivityEvent(
        event_type=ActivityEventType.TASK_COMPLETED,
        timestamp=record.completed_at,
        description=desc,
        related_ids={
            "task_id": str(record.task_id),
            "agent_id": str(record.agent_id),
        },
    )


def _task_metric_to_started_activity(
    record: TaskMetricRecord,
) -> ActivityEvent:
    """Convert a task metric with ``started_at`` to a task_started event.

    Caller must ensure ``record.started_at`` is not None.

    Returns:
        Result of type ``ActivityEvent``.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    if record.started_at is None:
        msg = "started_at must not be None"
        raise ValueError(msg)
    return ActivityEvent(
        event_type=ActivityEventType.TASK_STARTED,
        timestamp=record.started_at,
        description=f"Task {record.task_id} started",
        related_ids={
            "task_id": str(record.task_id),
            "agent_id": str(record.agent_id),
        },
    )


def _cost_record_to_activity(
    record: CostRecord,
    *,
    currency: str = DEFAULT_CURRENCY,
) -> ActivityEvent:
    """Convert a cost record to a cost_incurred activity event.

    Returns:
        Result of type ``ActivityEvent``.
    """
    desc = (
        f"API call to {record.model} "
        f"({record.input_tokens}+{record.output_tokens} tokens, "
        f"{format_cost_detail(record.cost, currency)})"
    )
    return ActivityEvent(
        event_type=ActivityEventType.COST_INCURRED,
        timestamp=record.timestamp,
        description=desc,
        related_ids={
            "agent_id": str(record.agent_id),
            "task_id": str(record.task_id),
        },
    )


def _tool_invocation_to_activity(
    record: ToolInvocationRecord,
) -> ActivityEvent:
    """Convert a tool invocation record to a tool_used activity event.

    Returns:
        Result of type ``ActivityEvent``.
    """
    if record.is_success:
        desc = f"Tool {record.tool_name} executed successfully"
    else:
        desc = f"Tool {record.tool_name} failed"
    related_ids: dict[str, str] = {
        "agent_id": str(record.agent_id),
    }
    if record.task_id is not None:
        related_ids["task_id"] = str(record.task_id)
    return ActivityEvent(
        event_type=ActivityEventType.TOOL_USED,
        timestamp=record.timestamp,
        description=desc,
        related_ids=related_ids,
    )


def _delegation_to_sent_activity(
    record: DelegationRecord,
) -> ActivityEvent:
    """Convert a delegation record to a delegation_sent activity event.

    Returns:
        Result of type ``ActivityEvent``.
    """
    return ActivityEvent(
        event_type=ActivityEventType.DELEGATION_SENT,
        timestamp=record.timestamp,
        description=(
            f"Delegated task {record.original_task_id} to {record.delegatee_id}"
        ),
        related_ids={
            "agent_id": str(record.delegator_id),
            "delegation_id": str(record.delegation_id),
            "delegatee_id": str(record.delegatee_id),
            "original_task_id": str(record.original_task_id),
            "delegated_task_id": str(record.delegated_task_id),
        },
    )


def _delegation_to_received_activity(
    record: DelegationRecord,
) -> ActivityEvent:
    """Convert a delegation record to a delegation_received activity event.

    Returns:
        Result of type ``ActivityEvent``.
    """
    return ActivityEvent(
        event_type=ActivityEventType.DELEGATION_RECEIVED,
        timestamp=record.timestamp,
        description=(
            f"Received delegation of task {record.original_task_id} "
            f"from {record.delegator_id}"
        ),
        related_ids={
            "agent_id": str(record.delegatee_id),
            "delegation_id": str(record.delegation_id),
            "delegator_id": str(record.delegator_id),
            "original_task_id": str(record.original_task_id),
            "delegated_task_id": str(record.delegated_task_id),
        },
    )


# ── Cost event redaction ────────────────────────────────────────

# Coupled to the format string in _cost_record_to_activity -- update
# both together if the description format changes.
_COST_DESC_PATTERN = re.compile(
    r"^API call to [^(]+ \((\d+\+\d+ tokens), [^)]+\)$",
)


def redact_cost_events(
    timeline: tuple[ActivityEvent, ...],
) -> tuple[ActivityEvent, ...]:
    """Redact model names and costs from cost_incurred event descriptions.

    Produces a new timeline with sensitive details stripped from
    ``cost_incurred`` event descriptions.  Non-cost events pass through
    unchanged.

    Args:
        timeline: Activity events (may contain cost_incurred events).

    Returns:
        Timeline with redacted cost event descriptions.
    """
    result: list[ActivityEvent] = []
    for event in timeline:
        if event.event_type == ActivityEventType.COST_INCURRED:
            match = _COST_DESC_PATTERN.match(event.description)
            if match:
                redacted = f"API call ({match.group(1)})"
            else:
                logger.warning(
                    HR_ACTIVITY_REDACTION_MISMATCH,
                    event_type=event.event_type.value,
                    description_length=len(event.description),
                )
                redacted = "API call (details redacted)"
            redacted_event = event.model_copy(
                update={"description": redacted},
            )
            result.append(redacted_event)
            continue
        result.append(event)
    return tuple(result)


# ── Timeline builders ────────────────────────────────────────────


def merge_activity_timeline(  # noqa: PLR0913
    lifecycle_events: tuple[AgentLifecycleEvent, ...],
    task_metrics: tuple[TaskMetricRecord, ...],
    *,
    cost_records: tuple[CostRecord, ...] = (),
    tool_invocations: tuple[ToolInvocationRecord, ...] = (),
    delegation_records_sent: tuple[DelegationRecord, ...] = (),
    delegation_records_received: tuple[DelegationRecord, ...] = (),
    currency: str = DEFAULT_CURRENCY,
) -> tuple[ActivityEvent, ...]:
    """Merge multiple event sources into a chronological activity timeline.

    Events are sorted by timestamp descending (most recent first).

    Args:
        lifecycle_events: Agent lifecycle events.
        task_metrics: Task completion metric records.
        cost_records: Per-API-call cost records.
        tool_invocations: Tool invocation records.
        delegation_records_sent: Delegation records (delegator perspective).
        delegation_records_received: Delegation records (delegatee perspective).
        currency: ISO 4217 currency code for cost formatting.

    Returns:
        Merged and sorted activity events.
    """
    activities: list[ActivityEvent] = [
        _lifecycle_to_activity(e) for e in lifecycle_events
    ]
    activities.extend(
        _task_metric_to_activity(r, currency=currency) for r in task_metrics
    )
    activities.extend(
        _task_metric_to_started_activity(r)
        for r in task_metrics
        if r.started_at is not None
    )
    activities.extend(
        _cost_record_to_activity(r, currency=currency) for r in cost_records
    )
    activities.extend(_tool_invocation_to_activity(r) for r in tool_invocations)
    activities.extend(_delegation_to_sent_activity(r) for r in delegation_records_sent)
    activities.extend(
        _delegation_to_received_activity(r) for r in delegation_records_received
    )
    activities.sort(key=lambda a: a.timestamp, reverse=True)
    return tuple(activities)


def filter_career_events(
    lifecycle_events: tuple[AgentLifecycleEvent, ...],
) -> tuple[CareerEvent, ...]:
    """Filter lifecycle events to career-relevant milestones only.

    Career events include: hired, fired, promoted, demoted, onboarded.
    Sorted by timestamp ascending (chronological career progression).

    Args:
        lifecycle_events: All lifecycle events for an agent.

    Returns:
        Career-relevant events in chronological order.
    """
    career: list[CareerEvent] = [
        CareerEvent(
            event_type=e.event_type,
            timestamp=e.timestamp,
            description=e.details or f"Agent {e.event_type.value}",
            initiated_by=e.initiated_by,
            metadata=e.metadata,
        )
        for e in lifecycle_events
        if e.event_type in _CAREER_EVENT_TYPES
    ]
    career.sort(key=lambda c: c.timestamp)
    return tuple(career)
