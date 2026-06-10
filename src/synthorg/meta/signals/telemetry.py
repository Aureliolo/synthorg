"""Telemetry signal aggregator.

Queries the :class:`TelemetryEventCounter` for event counts within the
observation window and returns a populated :class:`OrgTelemetrySummary`.
A missing counter (dev/test mode) yields an empty summary via the
safe-default path rather than raising.
"""

from datetime import datetime

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.signal_models import OrgTelemetrySummary
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_SIGNAL_AGGREGATION_COMPLETED,
    META_SIGNAL_AGGREGATION_FAILED,
)
from synthorg.telemetry.event_counter_protocol import TelemetryEventCounter

logger = get_logger(__name__)


class TelemetrySignalAggregator:
    """Aggregates telemetry events into org-wide summaries.

    Args:
        counter: Optional event counter to query.  When ``None`` (dev/
            test mode without a telemetry counter), aggregation yields
            an empty summary rather than failing.
    """

    def __init__(self, counter: TelemetryEventCounter | None = None) -> None:
        self._counter = counter

    @property
    def domain(self) -> NotBlankStr:
        """Signal domain name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("telemetry")

    async def aggregate(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgTelemetrySummary:
        """Aggregate telemetry signals for the time window.

        Args:
            since: Start of observation window.
            until: End of observation window.

        Returns:
            Org-wide telemetry summary; empty when no counter is
            wired or the window contains no events.
        """
        if self._counter is None:
            return OrgTelemetrySummary()
        try:
            summary = await self._counter.summarize(since=since, until=until)
            logger.info(
                META_SIGNAL_AGGREGATION_COMPLETED,
                domain="telemetry",
                event_count=summary.event_count,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger, META_SIGNAL_AGGREGATION_FAILED, exc, domain="telemetry"
            )
            return OrgTelemetrySummary()
        return summary
