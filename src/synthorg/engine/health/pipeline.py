"""Health monitoring pipeline composing judge, triage, and notifications.

The pipeline orchestrates the two-layer health monitoring design:
the ``HealthJudge`` (sensitive) emits tickets, the ``TriageFilter``
(conservative) dismisses or escalates, and escalated tickets are
dispatched to the ``NotificationDispatcher``.
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.health.models import (
    EscalationCause,
    EscalationSeverity,
    EscalationTicket,
)
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.health import HEALTH_PIPELINE_ERROR

if TYPE_CHECKING:
    from synthorg.engine.health.judge import HealthJudge
    from synthorg.engine.health.triage import TriageFilter
    from synthorg.engine.loop_protocol import TerminationReason
    from synthorg.engine.quality.models import StepQualitySignal
    from synthorg.notifications.protocol import NotificationSink

logger = get_logger(__name__)

# Map escalation causes to notification categories.
_CAUSE_TO_CATEGORY: dict[EscalationCause, NotificationCategory] = {
    EscalationCause.STAGNATION: NotificationCategory.HEALTH,
    EscalationCause.QUALITY_DEGRADATION: NotificationCategory.HEALTH,
    EscalationCause.REPEATED_FAILURE: NotificationCategory.HEALTH,
    EscalationCause.BUDGET_BREACH: NotificationCategory.BUDGET,
    EscalationCause.TIMEOUT: NotificationCategory.HEALTH,
}


class HealthMonitoringPipeline:
    """Two-layer health monitoring pipeline.

    Composes ``HealthJudge`` (sensitive) + ``TriageFilter``
    (conservative) + ``NotificationSink`` (delivery).

    Args:
        judge: The health judge instance.
        triage: The triage filter instance.
        notification_sink: Notification delivery target (typically
            a ``NotificationDispatcher`` for fan-out).
    """

    def __init__(
        self,
        *,
        judge: HealthJudge,
        triage: TriageFilter,
        notification_sink: NotificationSink,
    ) -> None:
        self._judge = judge
        self._triage = triage
        self._sink = notification_sink

    async def process(  # noqa: PLR0913
        self,
        *,
        termination_reason: TerminationReason,
        has_recovery: bool = False,
        quality_signals: tuple[StepQualitySignal, ...] = (),
        agent_id: str,
        task_id: str,
        execution_duration: float = 0.0,
    ) -> EscalationTicket | None:
        """Run the full health monitoring pipeline.

        Args:
            termination_reason: Why the execution loop terminated.
            has_recovery: Whether recovery was applied.
            quality_signals: Step-level quality signals.
            agent_id: Agent identifier.
            task_id: Task identifier.
            execution_duration: Wall-clock execution time.

        Returns:
            The escalation ticket if one was emitted and escalated,
            ``None`` if no ticket was emitted or it was dismissed.
        """
        try:
            return await self._process_inner(
                termination_reason=termination_reason,
                has_recovery=has_recovery,
                quality_signals=quality_signals,
                agent_id=agent_id,
                task_id=task_id,
                execution_duration=execution_duration,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger, HEALTH_PIPELINE_ERROR, exc, agent_id=agent_id, task_id=task_id
            )
            return None

    async def _process_inner(  # noqa: PLR0913
        self,
        *,
        termination_reason: TerminationReason,
        has_recovery: bool,
        quality_signals: tuple[StepQualitySignal, ...],
        agent_id: str,
        task_id: str,
        execution_duration: float,
    ) -> EscalationTicket | None:
        """Inner pipeline logic (no error swallowing).

        Returns:
            The escalated :class:`EscalationTicket` when the judge
            emits one and triage approves; ``None`` when the judge
            emits nothing or triage dismisses the ticket.
        """
        # Layer 1: judge emits ticket.
        ticket = self._judge.emit_ticket(
            termination_reason=termination_reason,
            has_recovery=has_recovery,
            quality_signals=quality_signals,
            agent_id=agent_id,
            task_id=task_id,
            execution_duration=execution_duration,
        )
        if ticket is None:
            return None

        # Layer 2: triage filters.
        if not self._triage.should_escalate(ticket):
            return None

        # Layer 3: dispatch notification (best-effort).
        notification = _ticket_to_notification(ticket)
        try:
            await self._sink.send(notification)
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                HEALTH_PIPELINE_ERROR,
                exc,
                agent_id=ticket.agent_id,
                task_id=ticket.task_id,
                ticket_id=ticket.id,
                detail="notification delivery failed, ticket still returned",
            )
        return ticket


def _ticket_to_notification(ticket: EscalationTicket) -> Notification:
    """Map an escalation ticket to a notification model.

    Returns:
        A :class:`Notification` mirroring the ticket's category,
        severity, evidence, and identifiers.
    """
    category = _CAUSE_TO_CATEGORY.get(
        ticket.cause,
        NotificationCategory.SYSTEM,
    )
    severity = _map_severity(ticket.severity)

    return Notification(
        category=category,
        severity=severity,
        title=(f"Health alert: {ticket.cause.value} ({ticket.severity.value})"),
        body=ticket.evidence,
        source="engine.health_monitor",
        metadata={
            "ticket_id": ticket.id,
            "agent_id": ticket.agent_id,
            "task_id": ticket.task_id,
            "cause": ticket.cause.value,
            "escalation_severity": ticket.severity.value,
        },
    )


_SEVERITY_MAP: dict[EscalationSeverity, NotificationSeverity] = {
    EscalationSeverity.LOW: NotificationSeverity.INFO,
    EscalationSeverity.MEDIUM: NotificationSeverity.WARNING,
    EscalationSeverity.HIGH: NotificationSeverity.ERROR,
    EscalationSeverity.CRITICAL: NotificationSeverity.CRITICAL,
}


def _map_severity(
    severity: EscalationSeverity,
) -> NotificationSeverity:
    """Map escalation severity to notification severity.

    Returns:
        The matching :class:`NotificationSeverity`; ``WARNING`` when
        no mapping exists for ``severity``.
    """
    return _SEVERITY_MAP.get(severity, NotificationSeverity.WARNING)
