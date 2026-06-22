"""Periodic pruner for the worker claim-dedup table.

``SeenClaimsRepository.prune_expired`` exists but nothing called it, so
the ``seen_claims`` table grew without bound for the life of a
distributed deployment. This backend-side lifecycle object prunes
expired rows on a fixed interval, only while the distributed queue is
enabled (the only producer of dedup rows).

It owns no NATS or HTTP surface: a single repository call on a timer,
fail-open so a transient DB error never wedges the loop.
"""

import asyncio
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.lifecycle_constants import DEFAULT_DRAIN_TIMEOUT_SECONDS
from synthorg.core.persistence_errors import QueryError
from synthorg.core.workers_errors import SeenClaimsPrunerUnrestartableError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workers import (
    WORKERS_SEEN_CLAIMS_PRUNE_FAILED,
    WORKERS_SEEN_CLAIMS_PRUNED,
    WORKERS_SEEN_CLAIMS_PRUNER_START_REJECTED,
    WORKERS_SEEN_CLAIMS_PRUNER_STARTED,
    WORKERS_SEEN_CLAIMS_PRUNER_STOPPED,
)
from synthorg.persistence.seen_claims_protocol import SeenClaimsRepository

logger = get_logger(__name__)

_MIN_INTERVAL_SECONDS: Final[float] = 1.0
"""Floor on the prune cadence so a misconfigured 0/None can never spin."""


class SeenClaimsPruner:
    """Deletes expired ``seen_claims`` rows on a fixed interval.

    Args:
        seen_claims: Durable dedup repository to prune.
        interval_seconds: Seconds between prune passes
            (``QueueConfig.prune_interval_seconds``).
        clock: Clock seam; ``FakeClock`` drives cadence in tests.
    """

    def __init__(
        self,
        *,
        seen_claims: SeenClaimsRepository,
        interval_seconds: float,
        clock: Clock | None = None,
    ) -> None:
        self._seen_claims = seen_claims
        self._interval_seconds = max(float(interval_seconds), _MIN_INTERVAL_SECONDS)
        self._clock: Clock = clock or SystemClock()
        self._running = False
        self._stop_event = asyncio.Event()  # lint-allow: loop-bound-init -- see Worker
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- ctx
        self._task: asyncio.Task[None] | None = None
        # Survives a timed-out stop so a later start() cannot stack a
        # second prune loop on the orphaned one.
        self._stop_failed = False

    @property
    def is_running(self) -> bool:
        """Whether the prune loop is active."""
        return self._running

    async def start(self) -> None:
        """Spawn the background prune loop. Non-blocking.

        Raises:
            RuntimeError: If already running (two loops would double the
                prune rate and contend on the write lock for no gain).
            SeenClaimsPrunerUnrestartableError: If a prior ``stop`` timed
                out and the pruner is now unrestartable.
        """
        async with self._lifecycle_lock:
            if self._stop_failed:
                logger.warning(
                    WORKERS_SEEN_CLAIMS_PRUNER_START_REJECTED,
                    reason="unrestartable",
                )
                raise SeenClaimsPrunerUnrestartableError
            if self._running:
                msg = "SeenClaimsPruner is already running"
                logger.warning(
                    WORKERS_SEEN_CLAIMS_PRUNER_START_REJECTED,
                    reason="already_running",
                    error_type=RuntimeError.__name__,
                )
                raise RuntimeError(msg)
            self._running = True
            self._stop_event.clear()
            self._task = asyncio.create_task(self._loop())
            logger.info(
                WORKERS_SEEN_CLAIMS_PRUNER_STARTED,
                interval_seconds=self._interval_seconds,
            )

    async def stop(self) -> None:
        """Stop the prune loop and await its exit. Idempotent.

        The lifecycle lock is held across the cancellation await so a
        concurrent ``start()`` waits for the stop to finish rather than
        observing the transient ``_running is True`` and raising a
        misleading "already running". This cannot deadlock: only
        ``start()`` / ``stop()`` acquire this lock and the loop never
        re-enters it.

        Raises:
            TimeoutError: If the prune-task drain exceeds the hard
                deadline; the pruner is then marked unrestartable.
        """
        async with self._lifecycle_lock:
            if not self._running:
                return
            self._stop_event.set()
            task = self._task
            if task is not None:
                task.cancel()

                async def _drain(drained: asyncio.Task[None]) -> None:
                    try:
                        await drained
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                        reraise_critical(exc)
                        logger.warning(
                            WORKERS_SEEN_CLAIMS_PRUNER_STOPPED,
                            error_type=type(exc).__name__,
                            error=safe_error_description(exc),
                            note="shutdown",
                        )

                drain_task: asyncio.Task[None] = asyncio.create_task(_drain(task))
                try:
                    await asyncio.wait_for(
                        asyncio.shield(drain_task),
                        timeout=DEFAULT_DRAIN_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    self._stop_failed = True
                    drain_task.cancel()
                    logger.error(
                        WORKERS_SEEN_CLAIMS_PRUNER_STOPPED,
                        error="stop exceeded hard deadline; pruner unrestartable",
                        timeout_seconds=DEFAULT_DRAIN_TIMEOUT_SECONDS,
                    )
                    raise
            self._running = False
            self._task = None
            logger.info(WORKERS_SEEN_CLAIMS_PRUNER_STOPPED)

    async def _loop(self) -> None:
        """Sleep-then-prune until stopped.

        Sleep-first so start-up is not a thundering prune; an empty
        table makes the first pass a no-op anyway.
        """
        # lint-allow: long-running-loop-kill-switch -- _stop_event drives shutdown.
        while not self._stop_event.is_set():
            await self._clock.sleep(self._interval_seconds)
            if self._stop_event.is_set():
                return
            await self._prune_once()

    async def _prune_once(self) -> int:
        """Run one prune pass; return rows removed. Fail-open.

        A transient ``QueryError`` is logged and swallowed: the next
        pass retries, and an unbounded-growth risk is strictly better
        than a crashed pruner that stops reclaiming space entirely.

        Returns:
            The number of expired rows removed (``0`` on a swallowed
            transient query error).
        """
        try:
            removed = await self._seen_claims.prune_expired(self._clock.now())
        except QueryError as exc:
            logger.warning(
                WORKERS_SEEN_CLAIMS_PRUNE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return 0
        if removed:
            logger.info(WORKERS_SEEN_CLAIMS_PRUNED, removed=removed)
        return removed
