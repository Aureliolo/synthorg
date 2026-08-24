# module-kind: code
"""Coordination and approval recording."""

from synthorg.observability import get_logger
from synthorg.observability.events.metrics import (
    METRICS_COORDINATION_RECORDED,
    METRICS_SCRAPE_FAILED,
)
from synthorg.observability.prometheus_labels import (
    VALID_APPROVAL_OUTCOMES,
    VALID_AUTONOMY_PROMOTION_OUTCOMES,
    require_label,
    require_non_negative,
)
from synthorg.observability.prometheus_recording._base import (
    _RecordingMetricsBase,
)

logger = get_logger(__name__)


class _CoordinationRecordingMixin(_RecordingMetricsBase):
    """Coordination and approval recording."""

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
