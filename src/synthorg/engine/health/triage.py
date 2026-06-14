"""Triage filter -- the conservative layer of health monitoring.

Filters escalation tickets before NotificationSink delivery.
Dismisses transient low-severity issues, escalates critical ones.
Rule-based only (deterministic, zero LLM cost).
"""

from typing import Final

from synthorg.engine.health.models import EscalationSeverity, EscalationTicket
from synthorg.observability import get_logger
from synthorg.observability.events.health import (
    HEALTH_TICKET_DISMISSED,
    HEALTH_TICKET_ESCALATED,
    HEALTH_TRIAGE_CONFIG_ERROR,
)

logger = get_logger(__name__)

# Thresholds for MEDIUM severity promotion to escalation. Named constants
# (not settings): the health pipeline is not yet wired into a boot-started
# service, so a registered setting would be ghost-wired.
_MEDIUM_STALL_DURATION_THRESHOLD: Final[float] = 60.0
_MEDIUM_STEPS_THRESHOLD: Final[int] = 5


class TriageFilter:
    """Conservative escalation triage filter.

    Dismisses transient issues, escalates persistent/critical ones.
    All rules are deterministic (no LLM).

    Rules:
        - LOW severity: always dismissed (transient blips).
        - MEDIUM severity: dismissed if stall < 60s AND steps < 5;
          escalated otherwise.
        - HIGH / CRITICAL: always escalated.

    Args:
        stall_duration_threshold: Minimum stall duration (seconds)
            for MEDIUM tickets to be escalated.
        steps_threshold: Minimum steps since last progress for
            MEDIUM tickets to be escalated.
    """

    def __init__(
        self,
        *,
        stall_duration_threshold: float = (_MEDIUM_STALL_DURATION_THRESHOLD),
        steps_threshold: int = _MEDIUM_STEPS_THRESHOLD,
    ) -> None:
        if stall_duration_threshold < 0:
            msg = (
                f"stall_duration_threshold must be >= 0, got {stall_duration_threshold}"
            )
            logger.warning(
                HEALTH_TRIAGE_CONFIG_ERROR,
                error=msg,
            )
            raise ValueError(msg)
        if steps_threshold < 0:
            msg = f"steps_threshold must be >= 0, got {steps_threshold}"
            logger.warning(
                HEALTH_TRIAGE_CONFIG_ERROR,
                error=msg,
            )
            raise ValueError(msg)
        self._stall_threshold = stall_duration_threshold
        self._steps_threshold = steps_threshold

    def should_escalate(self, ticket: EscalationTicket) -> bool:
        """Decide whether to escalate or dismiss a ticket.

        Args:
            ticket: The escalation ticket to evaluate.

        Returns:
            ``True`` to escalate (deliver to NotificationSink),
            ``False`` to dismiss (log and drop).
        """
        # HIGH and CRITICAL always escalate.
        if ticket.severity in (
            EscalationSeverity.HIGH,
            EscalationSeverity.CRITICAL,
        ):
            logger.info(
                HEALTH_TICKET_ESCALATED,
                ticket_id=ticket.id,
                severity=ticket.severity.value,
                cause=ticket.cause.value,
                reason="severity >= HIGH",
            )
            return True

        # LOW always dismissed.
        if ticket.severity == EscalationSeverity.LOW:
            logger.info(
                HEALTH_TICKET_DISMISSED,
                ticket_id=ticket.id,
                severity=ticket.severity.value,
                cause=ticket.cause.value,
                reason="LOW severity (transient)",
            )
            return False

        # MEDIUM: escalate if stall is long enough OR enough stuck steps.
        if (
            ticket.stall_duration_seconds >= self._stall_threshold
            or ticket.steps_since_last_progress >= self._steps_threshold
        ):
            logger.info(
                HEALTH_TICKET_ESCALATED,
                ticket_id=ticket.id,
                severity=ticket.severity.value,
                cause=ticket.cause.value,
                stall_seconds=ticket.stall_duration_seconds,
                steps_stuck=ticket.steps_since_last_progress,
                reason="MEDIUM with persistent stall",
            )
            return True

        logger.info(
            HEALTH_TICKET_DISMISSED,
            ticket_id=ticket.id,
            severity=ticket.severity.value,
            cause=ticket.cause.value,
            stall_seconds=ticket.stall_duration_seconds,
            steps_stuck=ticket.steps_since_last_progress,
            reason="MEDIUM transient (below thresholds)",
        )
        return False
