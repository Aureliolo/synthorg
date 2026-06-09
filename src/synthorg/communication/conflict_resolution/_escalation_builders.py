# module-kind: code
"""Pure builders for human-escalation notifications and terminal resolutions.

The resolver class holds the stateful await/cleanup orchestration; these
helpers are side-effect-free (apart from the escalation-outcome metric)
and depend solely on their arguments.
"""

from datetime import UTC, datetime

from synthorg.communication.conflict_resolution.escalation.models import Escalation
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictResolution,
    ConflictResolutionOutcome,
)
from synthorg.core.clock import Clock
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.observability.metrics_hub import record_escalation_outcome


def build_escalation_notification(
    escalation: Escalation,
    conflict: Conflict,
) -> Notification:
    """Render an operator-facing notification for the new escalation.

    Args:
        escalation: The PENDING escalation row.
        conflict: The conflict awaiting a human decision.

    Returns:
        The escalation ``Notification`` for operator delivery.
    """
    summary_lines = [f"Conflict subject: {conflict.subject}"]
    summary_lines.extend(
        f"- {position.agent_id} ({position.agent_department}, "
        f"{position.agent_level}): {position.position}"
        for position in conflict.positions
    )
    body = "\n".join(summary_lines)
    metadata: dict[str, object] = {
        "escalation_id": escalation.id,
        "conflict_id": conflict.id,
        "conflict_type": conflict.type.value,
        "subject": conflict.subject,
    }
    if conflict.task_id is not None:
        metadata["task_id"] = conflict.task_id
    if escalation.expires_at is not None:
        metadata["expires_at"] = escalation.expires_at.isoformat()
    return Notification(
        category=NotificationCategory.ESCALATION,
        severity=NotificationSeverity.WARNING,
        title=f"Conflict escalation pending: {conflict.id}",
        body=body,
        source="conflict_resolution.human_strategy",
        metadata=metadata,
    )


def timeout_resolution(
    conflict: Conflict,
    *,
    clock: Clock | None = None,
) -> ConflictResolution:
    """Resolution returned when no decision arrives in time.

    Args:
        conflict: The conflict that timed out.
        clock: Optional injectable time source for the ``resolved_at``
            stamp; defaults to system wall-clock time.

    Returns:
        An ESCALATED_TO_HUMAN resolution carrying the timeout reason.
    """
    reason = (
        "No human decision was collected before the escalation timeout. "
        "Conflict remains ESCALATED_TO_HUMAN; operators may still decide "
        "via the REST API."
    )
    record_escalation_outcome(outcome="escalated_to_human")
    return ConflictResolution(
        conflict_id=str(conflict.id),
        outcome=ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
        winning_agent_id=None,
        winning_position=None,
        decided_by="human",
        reasoning=reason,
        resolved_at=clock.now() if clock is not None else datetime.now(UTC),
    )


def cancelled_resolution(
    conflict: Conflict,
    *,
    clock: Clock | None = None,
) -> ConflictResolution:
    """Resolution returned when the resolver coroutine is cancelled.

    Args:
        conflict: The conflict whose resolver was cancelled.
        clock: Optional injectable time source for the ``resolved_at``
            stamp; defaults to system wall-clock time.

    Returns:
        An ESCALATED_TO_HUMAN resolution carrying the cancellation reason.
    """
    reason = (
        "Escalation resolver was cancelled before a human decision could be collected."
    )
    record_escalation_outcome(outcome="escalated_to_human")
    return ConflictResolution(
        conflict_id=str(conflict.id),
        outcome=ConflictResolutionOutcome.ESCALATED_TO_HUMAN,
        winning_agent_id=None,
        winning_position=None,
        decided_by="human",
        reasoning=reason,
        resolved_at=clock.now() if clock is not None else datetime.now(UTC),
    )
