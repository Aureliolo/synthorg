# module-kind: service
"""Sync-to-async bridge that durably persists audit-chain entries.

:class:`AuditChainSink.emit` is a synchronous structlog processor that
runs on whichever thread logged the event, so it cannot ``await`` a
persistence repository directly. This writer bridges the gap: ``emit``
calls :meth:`enqueue` (sync, thread-safe) after appending to the
in-memory chain, and a background asyncio task drains the queue and
``await``-appends each entry to the durable :class:`AuditChainRepository`.

At startup the in-memory chain is rehydrated from the repository via
:meth:`hydrate` so the tamper-evident tail hash and verification survive
restarts.
"""

import asyncio
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
        durable path (it remains in the live in-memory chain) and logged.
        """
        try:
            self._queue.put_nowait(entry)
        except queue.Full:
            logger.warning(
                AUDIT_CHAIN_PERSIST_ENQUEUE_DROPPED,
                position=entry.position,
            )

    async def hydrate(self, chain: HashChain) -> None:
        """Rebuild ``chain`` from the durable store at startup."""
        entries: list[ChainEntry] = []
        offset = 0
        while True:
            page = await self._repo.query(
                AuditChainFilterSpec(),
                limit=_HYDRATE_PAGE_SIZE,
                offset=offset,
            )
            entries.extend(page)
            if len(page) < _HYDRATE_PAGE_SIZE:
                break
            offset += _HYDRATE_PAGE_SIZE
        chain.restore(tuple(entries))
        logger.info(AUDIT_CHAIN_PERSIST_HYDRATED, entries=len(entries))

    async def start(self) -> None:
        """Spawn the background drain task. Idempotent."""
        if self._drain_task is not None:
            return
        self._drain_task = asyncio.create_task(self._drain())
        logger.info(AUDIT_CHAIN_PERSIST_STARTED)

    async def stop(self) -> None:
        """Stop the drain task, flushing queued entries first."""
        if self._drain_task is None:
            return
        self._queue.put(None)
        try:
            async with asyncio.timeout(_STOP_DRAIN_TIMEOUT_SECONDS):
                await self._drain_task
        except TimeoutError:
            self._drain_task.cancel()
        self._drain_task = None
        logger.info(AUDIT_CHAIN_PERSIST_STOPPED)

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
