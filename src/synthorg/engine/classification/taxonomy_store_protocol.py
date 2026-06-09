"""Protocol for the error taxonomy store.

The store records classification findings produced by the detector
pipeline and exposes a time-windowed query surface for the signals
aggregator layer.

Design:
- The store is a :class:`~synthorg.engine.classification.protocol.ClassificationSink`
  so it can be registered alongside the performance and notification
  sinks without a separate subscription plumbing.
- Query methods are async to leave room for a durable implementation
  that reaches out to persistence without changing the protocol.
- Summaries are produced in-store so aggregators stay thin; a single
  owner of the category-roll-up logic means consistent numbers no
  matter which caller (signals, analytics, dashboards) reads them.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from synthorg.engine.classification.models import ErrorFinding
from synthorg.engine.classification.protocol import ClassificationSink
from synthorg.meta.signal_models import OrgErrorSummary


@runtime_checkable
class ErrorTaxonomyStore(ClassificationSink, Protocol):
    """Stores classification results and produces time-windowed summaries.

    Inherits :class:`ClassificationSink` so static typing enforces the
    sink contract (the store *is* a registered sink; without the
    inheritance ``ErrorTaxonomyStore`` and ``ClassificationSink`` could
    silently drift apart).  Implementations must be safe to call from
    multiple concurrent tasks; the in-memory default uses a deque and
    an ``asyncio.Lock``.
    """

    async def query_findings(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[ErrorFinding, ...]:
        """Return findings classified within the window.

        Args:
            since: Start of the observation window (UTC).
            until: End of the observation window (UTC).

        Returns:
            Tuple of findings whose classified_at falls in
            ``[since, until)``.  Ordered newest-first.
        """
        ...

    async def summarize(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgErrorSummary:
        """Produce the org-wide error summary for the window.

        Args:
            since: Start of the observation window (UTC).
            until: End of the observation window (UTC).

        Returns:
            Populated :class:`OrgErrorSummary`; empty when the window
            contains no classifications.
        """
        ...

    async def count(self) -> int:
        """Return the number of classification results currently stored.

        Useful for diagnostics and the store-size guard in tests.
        """
        ...

    async def clear(self) -> None:
        """Drop all stored results.  Intended for test isolation."""
        ...
