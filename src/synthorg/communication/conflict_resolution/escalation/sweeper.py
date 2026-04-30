"""Background task that expires stale escalations (#1418).

Runs in the event loop at ``sweeper_interval_seconds`` cadence, calling
:meth:`EscalationQueueStore.mark_expired` so PENDING rows past their
``expires_at`` transition to ``EXPIRED`` without relying on the
resolver coroutine still being alive.

Crucial for restart recovery: after a process restart, any coroutine
that was awaiting a decision has died, but the escalation row remains
``PENDING`` in the store.  The sweeper will eventually reap it.
"""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.communication.conflict_resolution.escalation.protocol import (
    EscalationQueueStore,  # noqa: TC001
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_EXPIRED,
    CONFLICT_ESCALATION_SWEEPER_FAILED,
    CONFLICT_ESCALATION_SWEEPER_STARTED,
    CONFLICT_ESCALATION_SWEEPER_STOPPED,
)
from synthorg.settings.kill_switch import resolve_bool_with_fallback

if TYPE_CHECKING:
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


class EscalationExpirationSweeper:
    """Periodic background task that expires stale escalations."""

    def __init__(
        self,
        store: EscalationQueueStore,
        *,
        interval_seconds: float = 30.0,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        """Initialise the sweeper.

        Args:
            store: The queue store whose PENDING rows will be expired.
            interval_seconds: How often to run; must be >= 1 second.
            config_resolver: Optional resolver for the
                ``communication.escalation_sweeper_paused`` flag.  When
                wired, every sweep tick reads the flag so an operator
                can pause the sweeper at runtime without restart;
                without a resolver the loop runs unconditionally
                (matching the registered default of ``False``).
        """
        if interval_seconds < 1.0:
            msg = f"interval_seconds must be >= 1.0 (got {interval_seconds})"
            logger.warning(
                CONFLICT_ESCALATION_SWEEPER_FAILED,
                interval_seconds=interval_seconds,
                error=msg,
                note="invalid_config",
            )
            raise ValueError(msg)
        self._store = store
        self._interval = interval_seconds
        self._config_resolver = config_resolver
        self._task: asyncio.Task[None] | None = None
        # ``asyncio.Event`` and ``asyncio.Lock`` bind to the running
        # loop on first use.  Construction happens at app-wire time
        # (no loop yet) and the sweeper may also be reused across
        # multiple lifespans in tests, so create them lazily in
        # ``start()`` on the loop that will actually run the task.
        self._stop_event: asyncio.Event | None = None
        self._start_lock: asyncio.Lock | None = None

    async def start(self) -> None:
        """Schedule the background loop.

        Idempotent + concurrent-safe: concurrent ``start()`` calls
        serialize on an asyncio.Lock so at most one task is created
        even when multiple callers race.
        """
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        async with self._start_lock:
            if self._task is not None and not self._task.done():
                return
            self._stop_event.clear()
            self._task = asyncio.create_task(
                self._run(),
                name="escalation-sweeper",
            )
        logger.info(
            CONFLICT_ESCALATION_SWEEPER_STARTED,
            interval_seconds=self._interval,
        )

    async def stop(self) -> None:
        """Signal the loop to exit and await its completion."""
        if self._stop_event is not None:
            self._stop_event.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Expected: we just cancelled the task.
            pass
        except Exception as exc:
            # Best-effort shutdown: never propagate, but elevate to
            # WARNING so real failures surface in production logs
            # instead of being lost at DEBUG.
            logger.warning(
                CONFLICT_ESCALATION_SWEEPER_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="shutdown",
            )
        finally:
            self._task = None
            # Drop the loop-bound primitives so the next ``start()``
            # (potentially on a new event loop) can recreate them.
            self._stop_event = None
            self._start_lock = None
        logger.info(CONFLICT_ESCALATION_SWEEPER_STOPPED)

    async def _run(self) -> None:
        """Main loop body."""
        if self._stop_event is None:
            # Defensive: ``start()`` installs the event before
            # scheduling the task; a ``_run`` that sees ``None`` can
            # only be the result of ``stop()`` racing with ``start()``.
            return
        while not self._stop_event.is_set():
            try:
                await self._sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    CONFLICT_ESCALATION_SWEEPER_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval,
                )
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                # Explicit re-raise: external cancellation (stop() or
                # loop shutdown) must terminate the loop, not fall
                # through to another sweep iteration.
                raise

    async def _sweep_once(self) -> None:
        """Expire any rows whose deadline has passed.

        Emits the same event constant on every iteration (at ``info``
        when rows were expired, at ``debug`` when the pass was clean)
        so operators can detect a silent sweeper (store returns 0 due
        to a timezone / WHERE-clause bug) by the absence of debug logs.

        Honors the ``communication.escalation_sweeper_paused`` flag at
        the start of each tick: when ``True`` the loop stays resident
        but the call short-circuits so an operator can suspend
        expiration during incident investigation without tearing down
        the lifecycle plumbing.
        """
        paused = await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace="communication",
            key="escalation_sweeper_paused",
            fallback=False,
        )
        if paused:
            logger.debug(
                CONFLICT_ESCALATION_EXPIRED,
                expired_count=0,
                note="sweeper_paused_by_setting",
            )
            return
        now = datetime.now(UTC)
        expired = await self._store.mark_expired(now.isoformat())
        if expired:
            logger.info(
                CONFLICT_ESCALATION_EXPIRED,
                expired_count=len(expired),
                expired_ids=list(expired),
            )
        else:
            logger.debug(CONFLICT_ESCALATION_EXPIRED, expired_count=0)
