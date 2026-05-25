"""Health judge -- the sensitive layer of health monitoring.

Emits ``EscalationTicket`` instances when execution outcomes
indicate problems. Designed to be over-sensitive (false positives
are filtered by the triage layer).
"""

from synthorg.engine.health.models import (
    EscalationCause,
    EscalationSeverity,
    EscalationTicket,
)
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.quality.models import StepQuality, StepQualitySignal
from synthorg.observability import get_logger
from synthorg.observability.events.health import HEALTH_TICKET_EMITTED

logger = get_logger(__name__)

# Default threshold for consecutive INCORRECT steps triggering
# quality degradation escalation.
_DEFAULT_QUALITY_DEGRADATION_THRESHOLD: int = 3


class HealthJudge:
    """Sensitive health monitoring layer.

    Emits escalation tickets for problematic execution outcomes.
    Designed to be over-sensitive -- the triage filter handles
    dismissal of transient issues.

    Args:
        quality_degradation_threshold: Minimum consecutive INCORRECT
            quality signals to trigger a quality degradation ticket.
    """

    def __init__(
        self,
        *,
        quality_degradation_threshold: int = (_DEFAULT_QUALITY_DEGRADATION_THRESHOLD),
    ) -> None:
        if quality_degradation_threshold < 1:
            msg = (
                f"quality_degradation_threshold must be >= 1, "
                f"got {quality_degradation_threshold}"
            )
            logger.warning(
                HEALTH_TICKET_EMITTED,
                error=msg,
            )
            raise ValueError(msg)
        self._quality_threshold = quality_degradation_threshold

    def emit_ticket(  # noqa: PLR0913
        self,
        *,
        termination_reason: TerminationReason,
        has_recovery: bool = False,
        quality_signals: tuple[StepQualitySignal, ...] = (),
        agent_id: str,
        task_id: str,
        execution_duration: float = 0.0,
    ) -> EscalationTicket | None:
        """Evaluate execution outcome and emit a ticket if warranted.

        Args:
            termination_reason: Why the execution loop terminated.
            has_recovery: Whether a recovery strategy was applied.
            quality_signals: Step-level quality signals (may be empty).
            agent_id: Agent identifier.
            task_id: Task identifier.
            execution_duration: Wall-clock execution time in seconds.

        Returns:
            An ``EscalationTicket`` if the outcome warrants escalation,
            or ``None`` if the execution was healthy.
        """
        # Check 1: stagnation termination.
        if termination_reason == TerminationReason.STAGNATION:
            ticket = EscalationTicket(
                cause=EscalationCause.STAGNATION,
                severity=EscalationSeverity.HIGH,
                evidence=(
                    "Agent terminated due to stagnation "
                    "(repeated tool calls without progress)"
                ),
                agent_id=agent_id,
                task_id=task_id,
                stall_duration_seconds=execution_duration,
                quality_signals=quality_signals,
            )
            _log_ticket(ticket)
            return ticket

        # Check 2: error termination with recovery.
        if termination_reason == TerminationReason.ERROR and has_recovery:
            ticket = EscalationTicket(
                cause=EscalationCause.REPEATED_FAILURE,
                severity=EscalationSeverity.MEDIUM,
                evidence=("Agent execution failed and recovery was applied"),
                agent_id=agent_id,
                task_id=task_id,
                stall_duration_seconds=execution_duration,
                quality_signals=quality_signals,
            )
            _log_ticket(ticket)
            return ticket

        # Check 3: quality degradation from consecutive INCORRECT.
        consecutive = _count_trailing_incorrect(quality_signals)
        if consecutive >= self._quality_threshold:
            severity = (
                EscalationSeverity.CRITICAL
                if consecutive >= self._quality_threshold * 2
                else EscalationSeverity.HIGH
            )
            ticket = EscalationTicket(
                cause=EscalationCause.QUALITY_DEGRADATION,
                severity=severity,
                evidence=(
                    f"{consecutive} consecutive incorrect steps "
                    f"(threshold: {self._quality_threshold})"
                ),
                agent_id=agent_id,
                task_id=task_id,
                steps_since_last_progress=consecutive,
                stall_duration_seconds=execution_duration,
                quality_signals=quality_signals,
            )
            _log_ticket(ticket)
            return ticket

        return None


def _count_trailing_incorrect(
    signals: tuple[StepQualitySignal, ...],
) -> int:
    """Count consecutive INCORRECT signals from the end.

    Returns:
        The length of the trailing run of INCORRECT signals.
    """
    count = 0
    for signal in reversed(signals):
        if signal.quality == StepQuality.INCORRECT:
            count += 1
        else:
            break
    return count


def _log_ticket(ticket: EscalationTicket) -> None:
    """Log an emitted escalation ticket."""
    logger.info(
        HEALTH_TICKET_EMITTED,
        ticket_id=ticket.id,
        cause=ticket.cause.value,
        severity=ticket.severity.value,
        agent_id=ticket.agent_id,
        task_id=ticket.task_id,
        evidence=ticket.evidence,
    )
