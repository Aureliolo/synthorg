# module-kind: service
"""Sync-to-async bridge that durably persists audit-chain entries.

:meth:`AuditChainSink.emit` is the stdlib :class:`logging.Handler` method
that runs synchronously on whichever thread logged the event (after
structlog's processor chain completes), so it cannot ``await`` a
persistence repository directly. This writer bridges the gap: ``emit``
calls :meth:`enqueue` (sync, thread-safe) after appending to the
in-memory chain, and a background asyncio task drains the queue and
``await``-appends each entry to the durable :class:`AuditChainRepository`.

At startup the in-memory chain is rehydrated from the repository via
:meth:`hydrate` so the tamper-evident tail hash and verification survive
restarts.
"""

import asyncio
import contextlib
import queue
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.audit_chain.chain import ChainEntry, HashChain
from synthorg.observability.events.audit_chain import (
    AUDIT_CHAIN_PERSIST_DRAIN_FAILED,
    AUDIT_CHAIN_PERSIST_ENQUEUE_DROPPED,
    AUDIT_CHAIN_PERSIST_HYDRATED,
    AUDIT_CHAIN_PERSIST_STARTED,
    AUDIT_CHAIN_PERSIST_STOPPED,
)
from synthorg.persistence.audit_chain_protocol import (
    AuditChainFilterSpec,
    AuditChainRepository,
)

logger = get_logger(__name__)

# Bound the cross-thread queue so a stalled drain cannot grow it without
# limit. On overflow the entry stays in the in-memory chain (durability
# degrades for that one entry) and the drop is logged.
_DEFAULT_QUEUE_MAXSIZE: Final[int] = 10_000
_HYDRATE_PAGE_SIZE: Final[int] = 500
_STOP_DRAIN_TIMEOUT_SECONDS: Final[float] = 5.0


class DurableAuditChainWriter:
    """Drains in-memory audit-chain appends to a durable repository.

    Args:
        repo: The durable audit-chain repository.
        queue_maxsize: Bound on the cross-thread hand-off queue.
    """

    def __init__(
        self,
        repo: AuditChainRepository,
        *,
        queue_maxsize: int = _DEFAULT_QUEUE_MAXSIZE,
    ) -> None:
        self._repo = repo
        self._queue: queue.Queue[ChainEntry | None] = queue.Queue(maxsize=queue_maxsize)
        self._drain_task: asyncio.Task[None] | None = None

    def enqueue(self, entry: ChainEntry) -> None:
        """Hand one entry to the drain task (sync, thread-safe).

        Called from :meth:`AuditChainSink.emit` under the sink lock.
        Never blocks: on a full queue the entry is dropped from the
        durable path (it remains in the live in-memory chain) and logged
        at ERROR. The live chain then carries a position the durable store
        lacks, so post-restart :meth:`hydrate` verification will flag the
        gap (see :data:`AUDIT_CHAIN_PERSIST_INTEGRITY_FAILED`).
        """
        try:
            self._queue.put_nowait(entry)
        except queue.Full:
            logger.error(
                AUDIT_CHAIN_PERSIST_ENQUEUE_DROPPED,
                position=entry.position,
                note="durable path lost this entry; chain verification will gap",
            )

    async def hydrate(self, chain: HashChain) -> None:
        """Rebuild ``chain`` from the durable store at startup.

        Pages with a ``min_position`` cursor (not OFFSET) so each page is an
        indexed range scan on the ``position`` primary key; an unbounded
        chain hydrates in O(N) total rather than O(N^2). Restores the
        entries in-memory only; full verification (hash continuity AND
        signatures) is the caller's job, via
        :meth:`~synthorg.observability.audit_chain.sink.AuditChainSink.verify_chain`,
        so there is exactly one place that walks the chain and reports on it.
        """
        entries: list[ChainEntry] = []
        min_position: int | None = None
        # lint-allow: long-running-loop-kill-switch -- bounded startup pagination
        while True:
            page = await self._repo.query(
                AuditChainFilterSpec(min_position=min_position),
                limit=_HYDRATE_PAGE_SIZE,
            )
            entries.extend(page)
            if len(page) < _HYDRATE_PAGE_SIZE:
                break
            min_position = page[-1].position + 1
        chain.restore(tuple(entries))
        logger.info(AUDIT_CHAIN_PERSIST_HYDRATED, entries=len(entries))

    async def start(self) -> None:
        """Spawn the background drain task. Idempotent."""
        if self._drain_task is not None:
            return
        self._drain_task = asyncio.create_task(self._drain())
        logger.info(AUDIT_CHAIN_PERSIST_STARTED)

    async def stop(self) -> None:
        """Stop the drain task, flushing queued entries first.

        On a clean stop every queued entry is durably appended before the
        ``None`` sentinel returns. On a drain timeout the task is cancelled
        and any still-queued entries are lost; that outcome is logged at
        WARNING with the dropped count so it is never mistaken for a clean
        flush.
        """
        drain_task = self._drain_task
        if drain_task is None:
            return
        # Keep ``self._drain_task`` published until the drainer has fully
        # exited. Clearing it up front would let a concurrent ``start()`` see
        # ``None`` and spawn a second drainer on the same queue while this one
        # is still flushing (or blocked in ``repo.append()``); two drainers
        # would interleave durable appends and reorder the persisted chain.
        # Non-blocking: a blocking put on a saturated queue would stall the
        # event loop while the drain is mid-append. If the sentinel is
        # undelivered (queue full) the timeout below cancels the drain.
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(None)
        timed_out = False
        try:
            async with asyncio.timeout(_STOP_DRAIN_TIMEOUT_SECONDS):
                await drain_task
        except TimeoutError:
            timed_out = True
            drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drain_task
        finally:
            self._drain_task = None
        if timed_out:
            logger.warning(
                AUDIT_CHAIN_PERSIST_STOPPED,
                clean=False,
                reason="drain_timeout",
                entries_dropped=self._queue.qsize(),
                timeout_seconds=_STOP_DRAIN_TIMEOUT_SECONDS,
            )
        else:
            logger.info(AUDIT_CHAIN_PERSIST_STOPPED, clean=True)

    async def _drain(self) -> None:
        """Pop entries off the queue and durably append them.

        Blocks on the stdlib queue in a worker thread (so the event loop
        stays free) until an entry or the ``None`` stop sentinel arrives.
        Append failures are best-effort: they log and continue so one bad
        write never stalls the chain.

        Raises:
            CancelledError: When the drain task is cancelled on stop.
        """
        loop = asyncio.get_running_loop()
        # lint-allow: long-running-loop-kill-switch -- exits on None stop sentinel
        while True:
            entry = await loop.run_in_executor(None, self._queue.get)
            if entry is None:
                return
            try:
                await self._repo.append(entry)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    AUDIT_CHAIN_PERSIST_DRAIN_FAILED,
                    position=entry.position,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
