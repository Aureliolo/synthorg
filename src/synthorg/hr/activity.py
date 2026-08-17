"""Activity timeline models and pure functions for building agent timelines.

Merges lifecycle events, task metrics, cost records, tool invocations,
and delegation records into a unified chronological timeline, and
filters career-relevant events.
"""

import copy
import re
from typing import Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY, format_cost_detail
from synthorg.core.delegation_types import DelegationRecord
from synthorg.core.run_outcome import RunOutcome
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import ActivityEventType, LifecycleEventType
from synthorg.hr.models import AgentLifecycleEvent
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_ACTIVITY_REDACTION_MISMATCH
from synthorg.tools.invocation_record import ToolInvocationRecord

logger = get_logger(__name__)


class ActivityEvent(BaseModel):
    """Single event in an agent's activity timeline.

    ``description`` says what happened and names nobody: the references an event
    relates to travel in ``related_ids``, and the names they stand for are
    resolved at the read boundary into ``actor_name`` and ``subject_title``. A
    description that interpolated an id put a UUID in front of an operator, which
    is what ``api/_read_names`` exists to prevent, and a stored name would go
    stale the moment an agent was renamed or a task retitled.

    Attributes:
        event_type: Event category (e.g. ``"hired"``, ``"task_completed"``).
        timestamp: When the event occurred.
        description: Human-readable event description, free of identifiers.
        related_ids: Related entity identifiers (e.g. task_id, agent_id).
        actor_name: Display name of whoever acted, when the roster covers them.
        subject_title: Title of the task the event is about, when it has one.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    event_type: ActivityEventType = Field(description="Event category")
    timestamp: AwareDatetime = Field(description="When the event occurred")
    description: str = Field(
        default="",
        max_length=1024,
        description="Human-readable event description, free of identifiers",
    )
    related_ids: dict[str, str] = Field(
        default_factory=dict,
        description="Related entity identifiers",
    )
    actor_name: NotBlankStr | None = Field(
        default=None,
        description=(
            "Display name of whoever acted, resolved at the read boundary;"
            " None when nothing names them, which the surface words itself"
        ),
    )
    subject_title: NotBlankStr | None = Field(
        default=None,
        description=(
            "Title of the task this event concerns, resolved at the read"
            " boundary; None when the task is gone or unreadable"
        ),
    )

    @model_validator(mode="after")
    def _deep_copy_related_ids(self) -> Self:
        """Deep-copy related_ids so the frozen model cannot be aliased.

        Returns:
            The instance with ``related_ids`` deep-copied.
        """
        object.__setattr__(self, "related_ids", copy.deepcopy(self.related_ids))
        return self


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

    @model_validator(mode="after")
    def _deep_copy_metadata(self) -> Self:
        """Deep-copy metadata so the frozen model cannot be aliased.

        Returns:
            The instance with ``metadata`` deep-copied.
        """
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self


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


def _task_metric_outcome(
    record: TaskMetricRecord,
) -> tuple[ActivityEventType, str]:
    """Classify a run as the feed's event type and the word for its outcome.

    A stored ``run_outcome`` distinguishes an empty run (finished, produced
    nothing) from a hard failure; ``is_success`` alone collapses both. Records
    that predate outcome capture fall back to ``is_success``.

    Returns:
        The event type and the status word its description opens with.
    """
    if record.run_outcome == RunOutcome.EMPTY:
        return ActivityEventType.TASK_EMPTY, "produced no artifacts"
    if record.run_outcome == RunOutcome.FAILED or (
        record.run_outcome is None and not record.is_success
    ):
        return ActivityEventType.TASK_FAILED, "failed"
    return ActivityEventType.TASK_COMPLETED, "succeeded"


def _task_metric_to_activity(
    record: TaskMetricRecord,
    *,
    currency: str = DEFAULT_CURRENCY,
) -> ActivityEvent:
    """Convert a task metric record to a run-outcome-aware timeline event.

    The cost/duration suffix is omitted when the telemetry is unmeasured (a
    transition-sourced record carries a reliability outcome but no cost or
    latency), keeping the description truthful.

    Returns:
        Result of type ``ActivityEvent``.
    """
    event_type, status = _task_metric_outcome(record)
    # The task is named by ``subject_title``, which the read boundary resolves.
    # A description that named the task itself would have to name it by the id,
    # which is the one thing an operator surface never renders.
    desc = f"Task {status}"
    if record.duration_seconds is not None and record.cost is not None:
        # lint-allow: currency-aggregation -- formats this one record's own
        # cost in the resolved display ``currency`` (not ``record.currency``);
        # a single record, so no cross-record/currency aggregation occurs.
        cost_detail = format_cost_detail(record.cost, currency)
        desc += f" ({record.duration_seconds:.1f}s, {cost_detail})"
    return ActivityEvent(
        event_type=event_type,
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
        description="Task started",
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
    # Both owners are optional on the record: work the system does for itself
    # belongs to no agent and no task, and task_id is a real foreign key, so
    # the model leaves them unset rather than inventing an id. Rendering that
    # absence through str() would mint the invented id back, and "None" is
    # truthy, so it defeats every absent-owner fallback downstream instead of
    # reading as the absence it is.
    related_ids: dict[str, str] = {}
    if record.agent_id is not None:
        related_ids["agent_id"] = str(record.agent_id)
    if record.task_id is not None:
        related_ids["task_id"] = str(record.task_id)
    return ActivityEvent(
        event_type=ActivityEventType.COST_INCURRED,
        timestamp=record.timestamp,
        description=desc,
        related_ids=related_ids,
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
        # Both parties and both tasks are in ``related_ids``, which is what the
        # surface links through; naming them here would print their keys.
        description="Delegated a task",
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
        description="Received a delegated task",
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

# Coupled to the cost suffix in _task_metric_to_activity. A run's own duration
# is not money and stays; the amount beside it is what redaction exists for, and
# it rides a task-outcome event rather than a cost one, so restricting redaction
# to COST_INCURRED would leave the spend readable to every audience.
_TASK_COST_SUFFIX_PATTERN = re.compile(r" \((\d+\.\d+s), [^)]+\)$")

#: The events whose description can carry a spend figure without being about
#: spend, so redaction has to reach them too.
_SPEND_CARRYING_OUTCOMES: Final[frozenset[ActivityEventType]] = frozenset(
    {
        ActivityEventType.TASK_COMPLETED,
        ActivityEventType.TASK_FAILED,
        ActivityEventType.TASK_EMPTY,
    }
)


def _redacted_cost_description(event: ActivityEvent) -> str:
    """The description for a cost event with the model and the amount removed.

    Returns:
        The token count alone, or a blanket redaction when the description does
        not match the format it is written in.
    """
    match = _COST_DESC_PATTERN.match(event.description)
    if match:
        return f"API call ({match.group(1)})"
    logger.warning(
        HR_ACTIVITY_REDACTION_MISMATCH,
        event_type=event.event_type.value,
        description_length=len(event.description),
    )
    return "API call (details redacted)"


def redact_cost_events(
    timeline: tuple[ActivityEvent, ...],
) -> tuple[ActivityEvent, ...]:
    """Strip model names and spend from every description that carries them.

    Two shapes carry them: a ``cost_incurred`` event, which is about spend and
    is reduced to its token count, and a task-outcome event, whose description
    appends the run's cost beside its duration. The duration survives; the
    amount does not. Everything else passes through unchanged.

    Args:
        timeline: Activity events, in any mix.

    Returns:
        Timeline with every spend figure redacted.
    """
    result: list[ActivityEvent] = []
    for event in timeline:
        if event.event_type == ActivityEventType.COST_INCURRED:
            redacted = _redacted_cost_description(event)
        elif event.event_type in _SPEND_CARRYING_OUTCOMES:
            redacted = _TASK_COST_SUFFIX_PATTERN.sub(r" (\1)", event.description)
        else:
            result.append(event)
            continue
        result.append(event.model_copy(update={"description": redacted}))
    return tuple(result)


# ── Timeline builders ────────────────────────────────────────────


def merge_activity_timeline(
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
