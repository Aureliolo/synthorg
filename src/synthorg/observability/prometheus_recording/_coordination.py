# module-kind: code
"""Coordination / escalation / approval recording."""

from synthorg.observability import get_logger
from synthorg.observability.events.metrics import (
    METRICS_COORDINATION_RECORDED,
    METRICS_SCRAPE_FAILED,
)
from synthorg.observability.prometheus_labels import (
    VALID_APPROVAL_OUTCOMES,
    VALID_AUTONOMY_PROMOTION_OUTCOMES,
    VALID_ESCALATION_OUTCOMES,
    require_label,
    require_non_negative,
    validate_department,
)
from synthorg.observability.prometheus_recording._base import (
    _RecordingMetricsBase,
)

logger = get_logger(__name__)


class _CoordinationRecordingMixin(_RecordingMetricsBase):
    """Coordination / escalation / approval recording."""

    def record_coordination_metrics(
        self,
        *,
        efficiency: float,
        overhead_percent: float,
    ) -> None:
        """Update coordination gauges after a multi-agent execution.

        Called by ``CoordinationCollector`` post-execution.

        Args:
            efficiency: Coordination efficiency ratio (0.0-1.0).
            overhead_percent: Coordination overhead percentage.

        Raises:
            ValueError: If either input is NaN, Inf, or negative, or
                if ``efficiency`` exceeds 1.0 (the documented upper
                bound for the ratio).
        """
        require_non_negative("record_coordination_metrics: efficiency", efficiency)
        if efficiency > 1.0:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="coordination_metrics",
                reason="efficiency_out_of_range",
                efficiency=efficiency,
            )
            msg = (
                f"record_coordination_metrics: efficiency must be <= 1.0;"
                f" got {efficiency!r}"
            )
            raise ValueError(msg)
        require_non_negative(
            "record_coordination_metrics: overhead_percent",
            overhead_percent,
        )
        self._coordination_efficiency.set(efficiency)
        self._coordination_overhead_percent.set(overhead_percent)
        logger.debug(
            METRICS_COORDINATION_RECORDED,
            efficiency=efficiency,
            overhead_percent=overhead_percent,
        )

    def record_escalation_queue_depth(
        self,
        *,
        department: str,
        depth: int,
    ) -> None:
        """Set the escalation queue depth gauge for a department.

        ``department`` is validated against the live registry snapshot
        seeded by :meth:`refresh`; unknown values raise ``ValueError``
        and are dropped via the metrics-hub safe-record decorator.

        Args:
            department: Department name owning the escalation queue.
            depth: Current count of pending escalations.

        Raises:
            ValueError: If ``department`` is empty or not in the registry
                snapshot, or ``depth`` is negative, NaN, or infinite.
        """
        if not department:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="escalation_queue_depth",
                reason="empty_department",
            )
            msg = "record_escalation_queue_depth: department must be non-empty"
            raise ValueError(msg)
        validate_department(department)
        require_non_negative("record_escalation_queue_depth: depth", depth)
        self._escalation_queue_depth.labels(department=department).set(depth)

    def record_escalation_outcome(self, *, outcome: str) -> None:
        """Increment the escalation-outcome counter.

        Args:
            outcome: One of ``"resolved"`` / ``"escalated_to_human"``
                / ``"auto_resolved"`` / ``"notify_failed"`` /
                ``"sweeper_failed"``.

        Raises:
            ValueError: If *outcome* is not in
                :data:`VALID_ESCALATION_OUTCOMES`.
        """
        require_label("escalation outcome", outcome, VALID_ESCALATION_OUTCOMES)
        self._escalation_outcomes.labels(outcome=outcome).inc()

    def record_approval_decision(self, *, outcome: str) -> None:
        """Increment the approval-decision counter.

        Args:
            outcome: One of ``"approved"`` / ``"rejected"`` /
                ``"expired"``.

        Raises:
            ValueError: If *outcome* is not in
                :data:`VALID_APPROVAL_OUTCOMES`.
        """
        require_label("approval outcome", outcome, VALID_APPROVAL_OUTCOMES)
        self._approval_decisions.labels(outcome=outcome).inc()

    def record_autonomy_promotion(self, *, outcome: str) -> None:
        """Increment the autonomy-promotion-decision counter.

        Args:
            outcome: One of ``"granted"`` / ``"denied"``.

        Raises:
            ValueError: If *outcome* is not in
                :data:`VALID_AUTONOMY_PROMOTION_OUTCOMES`.
        """
        require_label(
            "autonomy promotion outcome", outcome, VALID_AUTONOMY_PROMOTION_OUTCOMES
        )
        self._autonomy_promotion_decisions.labels(outcome=outcome).inc()
