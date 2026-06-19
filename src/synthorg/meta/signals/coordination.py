"""Coordination metrics signal aggregator.

Wraps the composable coordination metrics store to produce an
OrgCoordinationSummary by averaging the per-run metrics in a window.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Final

from synthorg.budget.coordination_store import (
    CoordinationMetricsRecord,
    CoordinationMetricsStore,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.signal_models import OrgCoordinationSummary
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_SIGNAL_AGGREGATION_COMPLETED,
    META_SIGNAL_AGGREGATION_FAILED,
)

logger = get_logger(__name__)

_QUERY_LIMIT: Final[int] = 10000

_EMPTY = OrgCoordinationSummary()


def _mean(values: Sequence[float]) -> float | None:
    """Return the arithmetic mean of *values*, or ``None`` when empty.

    Returns:
        The mean, or ``None``.
    """
    if not values:
        return None
    return sum(values) / len(values)


class CoordinationSignalAggregator:
    """Aggregates coordination metrics into org-wide summaries.

    Args:
        store: Coordination metrics store queried per window; ``None``
            degrades the domain to an empty summary.
    """

    def __init__(self, store: CoordinationMetricsStore | None = None) -> None:
        self._store = store

    @property
    def domain(self) -> NotBlankStr:
        """Signal domain name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("coordination")

    async def aggregate(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgCoordinationSummary:
        """Aggregate coordination signals for the time window.

        Averages each of the per-run coordination metrics across the
        records in ``[since, until]``. A metric absent from every record
        stays ``None``. Degrades to an empty summary when no store is
        wired or on any failure.

        Args:
            since: Start of observation window.
            until: End of observation window.

        Returns:
            Org-wide coordination summary.
        """
        if self._store is None:
            logger.info(META_SIGNAL_AGGREGATION_COMPLETED, domain="coordination")
            return _EMPTY
        try:
            records, _ = self._store.query(since=since, until=until, limit=_QUERY_LIMIT)
            return self._summarise(records)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger, META_SIGNAL_AGGREGATION_FAILED, exc, domain="coordination"
            )
            return _EMPTY

    def _summarise(
        self,
        records: Sequence[CoordinationMetricsRecord],
    ) -> OrgCoordinationSummary:
        """Average the per-run metrics across *records*.

        Returns:
            The org-wide coordination summary.
        """
        if not records:
            logger.info(META_SIGNAL_AGGREGATION_COMPLETED, domain="coordination")
            return _EMPTY
        metrics = [r.metrics for r in records]
        summary = OrgCoordinationSummary(
            coordination_efficiency=_mean(
                [m.efficiency.value for m in metrics if m.efficiency is not None]
            ),
            coordination_overhead_pct=_mean(
                [m.overhead.value_percent for m in metrics if m.overhead is not None]
            ),
            error_amplification=_mean(
                [
                    m.error_amplification.value
                    for m in metrics
                    if m.error_amplification is not None
                ]
            ),
            message_density=_mean(
                [
                    m.message_density.value
                    for m in metrics
                    if m.message_density is not None
                ]
            ),
            redundancy_rate=_mean(
                [
                    m.redundancy_rate.value
                    for m in metrics
                    if m.redundancy_rate is not None
                ]
            ),
            straggler_gap_ratio=_mean(
                [
                    m.straggler_gap.gap_ratio
                    for m in metrics
                    if m.straggler_gap is not None
                ]
            ),
            sample_count=len(records),
        )
        logger.info(
            META_SIGNAL_AGGREGATION_COMPLETED,
            domain="coordination",
            sample_count=len(records),
        )
        return summary
