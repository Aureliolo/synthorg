# module-kind: code
"""Keeping a latching failure across the restart that used to clear it.

:mod:`synthorg.providers.health_tracker` holds every call outcome in
memory, which is right for almost all of them: high-volume, decaying within
minutes, and costing one fresh measurement to lose. A latching failure is
the opposite on all three counts, and its own reason text says *this does
not clear without an operator*. A restart is not an operator.

So this is the write-through and read-back, kept beside the tracker rather
than inside it: the tracker's job is the window, the lock and the memory
bound, and a database round trip on the record path is a different concern
with a different failure posture. Restored latches re-enter as the outcomes
they were recorded as, so the verdict stays derived from one sequence of
records and nothing new decides whether a pair can serve.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.pagination import collect_all
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_LATCH_PERSIST_FAILED,
    PROVIDER_LATCH_RESTORE_FAILED,
    PROVIDER_LATCH_RESTORED,
)
from synthorg.persistence.provider_latch_protocol import ProviderLatchRepository
from synthorg.providers.health import HEALTH_WINDOW_HOURS, ProviderHealthRecord
from synthorg.providers.latch import LatchedFailure

logger = get_logger(__name__)

type RecordSink = Callable[[ProviderHealthRecord], Awaitable[None]]


class DurableLatches:
    """The durable half of the tracker's latching failures.

    Args:
        store: Repository holding one row per latched ``(provider, model)``.
    """

    __slots__ = ("_store",)

    def __init__(self, store: ProviderLatchRepository) -> None:
        self._store = store

    async def persist(self, record: ProviderHealthRecord) -> None:
        """Write *record* through when it establishes a latch.

        Failure-tolerant: the in-memory latch already stands, so the pair
        is out either way. What a failed write costs is the latch surviving
        the next restart, which is exactly the gap this closes, so it is
        logged at ERROR rather than swallowed.
        """
        latch = LatchedFailure.from_record(record)
        if latch is None:
            return
        try:
            await self._store.save(latch)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised; see docstring
            reraise_critical(exc)
            logger.error(
                PROVIDER_LATCH_PERSIST_FAILED,
                operation="write_through",
                provider=str(latch.provider_name),
                model=str(latch.model),
                outcome_class=latch.outcome_class.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def restore(self, sink: RecordSink, *, now: datetime | None = None) -> int:
        """Feed the still-standing latches back into *sink*.

        A row older than the record window can never be honoured again: the
        latch lookback is bounded by that window precisely so it cannot
        expire by eviction, so past it the lookback has already released the
        pair. Deleting it here is that release doing its own housekeeping,
        not a second expiry rule.

        Args:
            sink: Where a restored outcome goes, normally the tracker's own
                ``record``.
            now: Reference time the lookback is measured back from.

        Returns:
            How many latches came back still standing.
        """
        stored = await self._load()
        cutoff = (now or datetime.now(UTC)) - timedelta(hours=HEALTH_WINDOW_HOURS)
        live = [latch for latch in stored if latch.occurred_at >= cutoff]
        for expired in (latch for latch in stored if latch.occurred_at < cutoff):
            await self._release(expired)
        for latch in live:
            await sink(latch.to_record())
        if live:
            # INFO, not DEBUG: the verdict says it does not clear without an
            # operator, and an operator who restarted for an unrelated reason
            # needs the line that says it is still standing.
            logger.info(
                PROVIDER_LATCH_RESTORED,
                restored=len(live),
                expired=len(stored) - len(live),
                pairs=sorted(f"{latch.provider_name}/{latch.model}" for latch in live),
            )
        return len(live)

    async def _load(self) -> tuple[LatchedFailure, ...]:
        """Read every stored latch, degrading to none on a failed read.

        Returns:
            The stored latches, or an empty tuple when the read failed.
        """
        try:
            return await collect_all(
                lambda limit, offset: self._store.list_items(limit=limit, offset=offset)
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROVIDER_LATCH_RESTORE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()

    async def _release(self, latch: LatchedFailure) -> None:
        """Delete a latch the lookback has already released.

        Failure-tolerant: an undeleted row is read again next boot and found
        expired again, so the cost is one stale row rather than a wrong
        verdict, and failing the boot over it would be worse.
        """
        try:
            await self._store.delete(latch.pair)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised; see docstring
            reraise_critical(exc)
            logger.warning(
                PROVIDER_LATCH_PERSIST_FAILED,
                operation="expire",
                provider=str(latch.provider_name),
                model=str(latch.model),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


__all__ = ["DurableLatches", "RecordSink"]
