"""Coordination metrics signal aggregator.

Wraps the 9 composable coordination metrics to produce an
OrgCoordinationSummary.
"""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import OrgCoordinationSummary
from synthorg.observability import get_logger
from synthorg.observability.events.meta import META_SIGNAL_AGGREGATION_COMPLETED

if TYPE_CHECKING:
    from datetime import datetime

logger = get_logger(__name__)

_EMPTY = OrgCoordinationSummary()


class CoordinationSignalAggregator:
    """Aggregates coordination metrics into org-wide summaries."""

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

        Args:
            since: Start of observation window.
            until: End of observation window.

        Returns:
            Org-wide coordination summary.
        """
        _ = since, until  # Will be used by real implementation.
        logger.info(
            META_SIGNAL_AGGREGATION_COMPLETED,
            domain="coordination",
        )
        return _EMPTY
