"""Evolution outcomes signal aggregator.

Queries the :class:`EvolutionOutcomeStore` for proposal outcomes within
the observation window and returns a populated
:class:`OrgEvolutionSummary`.  A missing store (dev/test mode) yields
an empty summary via the safe-default path rather than raising.
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.signal_models import OrgEvolutionSummary
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_SIGNAL_AGGREGATION_COMPLETED,
    META_SIGNAL_AGGREGATION_FAILED,
)

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.meta.evolution.outcome_store_protocol import (
        EvolutionOutcomeStore,
    )

logger = get_logger(__name__)

_EMPTY = OrgEvolutionSummary()


class EvolutionSignalAggregator:
    """Aggregates evolution outcomes into org-wide summaries.

    Args:
        store: Optional outcome store to query.  When ``None`` (dev/
            test mode without a self-improvement cycle), aggregation
            yields an empty summary rather than failing.
    """

    def __init__(self, store: EvolutionOutcomeStore | None = None) -> None:
        self._store = store

    @property
    def domain(self) -> NotBlankStr:
        """Signal domain name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("evolution")

    async def aggregate(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgEvolutionSummary:
        """Aggregate evolution signals for the time window.

        Args:
            since: Start of observation window.
            until: End of observation window.

        Returns:
            Org-wide evolution summary; empty when no store is wired
            or the window contains no recorded outcomes.
        """
        if self._store is None:
            return _EMPTY
        try:
            summary = await self._store.summarize(since=since, until=until)
            logger.info(
                META_SIGNAL_AGGREGATION_COMPLETED,
                domain="evolution",
                total_proposals=summary.total_proposals,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger, META_SIGNAL_AGGREGATION_FAILED, exc, domain="evolution"
            )
            return _EMPTY
        return summary
