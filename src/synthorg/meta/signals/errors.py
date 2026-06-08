"""Error taxonomy signal aggregator.

Queries the :class:`ErrorTaxonomyStore` for findings within the
observation window and returns a populated :class:`OrgErrorSummary`.
A missing store (e.g. in dev/test mode) yields an empty summary via
the safe-default path rather than raising.
"""

from datetime import datetime

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.classification.taxonomy_store_protocol import ErrorTaxonomyStore
from synthorg.meta.signal_models import OrgErrorSummary
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_SIGNAL_AGGREGATION_COMPLETED,
    META_SIGNAL_AGGREGATION_FAILED,
)

logger = get_logger(__name__)

_EMPTY = OrgErrorSummary()


class ErrorSignalAggregator:
    """Aggregates error taxonomy findings into org-wide summaries.

    Args:
        store: Optional store to query.  When ``None`` (dev/test mode
            with no classification pipeline wired), aggregation yields
            an empty summary rather than failing.
    """

    def __init__(self, store: ErrorTaxonomyStore | None = None) -> None:
        self._store = store

    @property
    def domain(self) -> NotBlankStr:
        """Signal domain name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("errors")

    async def aggregate(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgErrorSummary:
        """Aggregate error signals for the time window.

        Args:
            since: Start of observation window.
            until: End of observation window.

        Returns:
            Org-wide error summary; empty when no store is wired or
            the window contains no classifications.
        """
        if self._store is None:
            return _EMPTY
        try:
            summary = await self._store.summarize(since=since, until=until)
            logger.info(
                META_SIGNAL_AGGREGATION_COMPLETED,
                domain="errors",
                total_findings=summary.total_findings,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger, META_SIGNAL_AGGREGATION_FAILED, exc, domain="errors"
            )
            return _EMPTY
        return summary
