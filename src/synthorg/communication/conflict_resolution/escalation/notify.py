"""Cross-instance wake-up for the human escalation queue (#1418 / #1444).

When the escalation queue runs on a shared database (currently Postgres)
and the API is deployed across multiple workers/pods, a resolver
awaiting a Future on worker A must be woken when an operator submits a
decision through worker B.  The Future itself is process-local
(:class:`PendingFuturesRegistry`), so the wake signal has to travel
through the shared database.

This module provides the :class:`EscalationNotifySubscriber` abstract
contract and a Postgres implementation that subscribes to a LISTEN
channel populated by triggers on the ``conflict_escalations`` table.
SQLite/in-memory backends have no cross-instance concern, so the
factory returns a :class:`NoopEscalationNotifySubscriber`.
"""

import asyncio
import re
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
    CONFLICT_ESCALATION_SUBSCRIBER_STARTED,
    CONFLICT_ESCALATION_SUBSCRIBER_STOPPED,
)

if TYPE_CHECKING:
    from synthorg.communication.conflict_resolution.escalation.protocol import (
        EscalationQueueStore,
    )
    from synthorg.communication.conflict_resolution.escalation.registry import (
        PendingFuturesRegistry,
    )

logger = get_logger(__name__)

# Safe Postgres unquoted-identifier pattern.  Defence-in-depth: the
# config layer validates this too, but the subscriber re-checks so a
# hand-constructed subscriber cannot inject unsafe SQL via
# ``LISTEN "<channel>"``.
_SAFE_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$",
)
_MAX_IDENTIFIER_LEN: Final[int] = 63


@runtime_checkable
class EscalationNotifySubscriber(Protocol):
    """Contract for cross-instance escalation wake-up subscribers.

    Implementations listen on a backend-specific signal (Postgres
    LISTEN/NOTIFY, NATS subjects, etc.) and forward state transitions
    to an in-process :class:`PendingFuturesRegistry` so any local
    resolver awaiting the escalation wakes with the correct payload.
    """

    async def start(self) -> None:
        """Begin subscribing.  Must be idempotent."""
        ...

    async def stop(self) -> None:
        """Stop subscribing and release resources.  Must be idempotent."""
        ...


class NoopEscalationNotifySubscriber:
    """No-op subscriber for single-worker / in-memory deployments."""

    async def start(self) -> None:
        """Noop."""
        return

    async def stop(self) -> None:
        """Noop."""
        return


class PostgresEscalationNotifySubscriber:
    """Subscribes to a Postgres LISTEN channel and wakes local futures.

    The Postgres ``conflict_escalations`` schema installs triggers that
    ``NOTIFY`` on the configured channel whenever a row transitions out
    of PENDING.  This subscriber fans those notifications out to the
    local :class:`PendingFuturesRegistry`: DECIDED rows cause
    ``registry.resolve`` (with the decision payload read from the row);
    EXPIRED/CANCELLED rows cause ``registry.cancel`` so any local
    resolver awaiting the Future is promptly unblocked.

    The subscriber is best-effort: connection failures are logged and
    the loop reconnects with a short back-off, never propagating to the
    application.  Missing a signal is not catastrophic because each
    resolver has its own ``timeout_seconds`` deadline and the
    :class:`EscalationExpirationSweeper` eventually reaps stale rows.
    """

    def __init__(
        self,
        repo: EscalationQueueStore,
        registry: PendingFuturesRegistry,
        *,
        channel: str,
        reconnect_delay_seconds: float = 1.0,
    ) -> None:
        """Initialise the subscriber.

        Args:
            repo: Escalation queue store (protocol-typed).  Postgres
                implementations deliver real NOTIFY payloads via
                ``subscribe_notifications``; other backends either
                return a backend-specific stream or raise
                ``RuntimeError`` from ``subscribe_notifications`` when
                subscriptions are unsupported (see the contract in
                ``synthorg.communication.conflict_resolution.escalation.protocol``).
            registry: Process-local registry whose futures should wake.
            channel: LISTEN/NOTIFY channel name.
            reconnect_delay_seconds: Seconds to wait before reconnecting
                after a connection failure.  Must be positive.
        """
        if reconnect_delay_seconds <= 0:
            msg = "reconnect_delay_seconds must be > 0"
            raise ValueError(msg)
        # Defensive: config.py already validates the channel, but a
        # hand-constructed subscriber must not be able to inject SQL
        # via ``LISTEN "<channel>"``.
        if (
            not channel
            or len(channel) > _MAX_IDENTIFIER_LEN
            or _SAFE_IDENTIFIER_PATTERN.fullmatch(channel) is None
        ):
            msg = (
                f"notify channel {channel!r} is not a safe Postgres identifier "
                "(must match ^[A-Za-z_][A-Za-z0-9_]*$, max 63 chars)"
            )
            raise ValueError(msg)
        self._repo = repo
        self._registry = registry
        self._channel = channel
        self._reconnect_delay = reconnect_delay_seconds
        self._task: asyncio.Task[None] | None = None
        # Lazy-init: asyncio primitives bind to the running loop on
        # first use, but this subscriber is wired at app-build time
        # (no loop) and may outlive one lifespan in tests that share
        # an app across loops.  Create on ``start()``, drop on ``stop()``.
        self._stop_event: asyncio.Event | None = None
        self._start_lock: asyncio.Lock | None = None

    async def start(self) -> None:
        """Schedule the background subscriber loop.

        Per the canonical lifecycle pattern
        (``docs/reference/lifecycle-sync.md``), the lock is held
        across the full body including the success log so concurrent
        ``start()`` / ``stop()`` calls cannot interleave.
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
                name="escalation-notify-subscriber",
            )
            logger.info(
                CONFLICT_ESCALATION_SUBSCRIBER_STARTED,
                channel=self._channel,
            )

    async def stop(self) -> None:
        """Signal the loop to exit and await its completion.

        Acquires ``_start_lock`` so a concurrent ``start()`` cannot
        recreate the task mid-stop. Per
        ``docs/reference/lifecycle-sync.md``, lifecycle locks must be
        held across the full body of both ``start`` and ``stop``.

        Idle stop: when nothing was ever started (``_task`` and
        ``_stop_event`` both None) we return immediately without
        allocating ``_start_lock``. Allocating an asyncio.Lock here
        would bind it to the current event loop; if the next ``start``
        runs on a different loop (test pattern with fresh-per-test
        loops) the orphan lock causes ``RuntimeError: ... is bound to
        a different event loop``.
        """
        if self._task is None and self._stop_event is None:
            return
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._stop_event is not None:
                self._stop_event.set()
            task = self._task
            if task is None:
                return
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    note="shutdown",
                )
            finally:
                self._task = None
                self._stop_event = None
            logger.info(CONFLICT_ESCALATION_SUBSCRIBER_STOPPED)
        # Release-and-clear last so a racing ``start()`` observes the
        # cleared task before recreating the lock primitive.
        self._start_lock = None

    async def _run(self) -> None:
        """Main loop: (re)open a listen connection and dispatch notifies."""
        if self._stop_event is None:
            return
        while not self._stop_event.is_set():
            try:
                await self._listen_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                    channel=self._channel,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._reconnect_delay,
                )
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise

    async def _listen_once(self) -> None:
        """Open a dedicated connection, LISTEN, and dispatch notifies.

        The actual LISTEN / UNLISTEN / autocommit plumbing lives in
        :meth:`PostgresEscalationRepository.subscribe_notifications`;
        this method just iterates the payload stream and forwards each
        notification to the in-process registry. The dedicated pool
        connection is still held for the subscription lifetime -- pool
        sizing guidance in the class docstring remains accurate.
        """
        async with self._repo.subscribe_notifications(self._channel) as payloads:
            async for payload in payloads:
                if self._stop_event is None or self._stop_event.is_set():
                    break
                await self._dispatch_payload(payload)

    async def _dispatch_payload(self, payload: str) -> None:
        """Interpret a NOTIFY payload and wake the local future."""
        # Payload format: "<escalation_id>:<new_status>" where status is
        # one of decided/expired/cancelled.  ``str.partition`` is
        # infallible, so no try/except around it -- malformed payloads
        # surface as empty ``escalation_id`` / ``status`` below.
        escalation_id, _, status = payload.partition(":")
        if not escalation_id or not status:
            logger.warning(
                CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                note="bad_payload",
                payload=payload,
            )
            return
        try:
            if status == "decided":
                row = await self._repo.get(escalation_id)
                if row is None or row.decision is None:
                    return
                await self._registry.resolve(escalation_id, row.decision)
            elif status in {"expired", "cancelled"}:
                await self._registry.cancel(escalation_id)
            else:
                # Unknown status -- surface so operators catch schema
                # drift (trigger/repo publishing an unrecognised code).
                logger.warning(
                    CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                    escalation_id=escalation_id,
                    status=status,
                    note="unknown_notify_status",
                )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                CONFLICT_ESCALATION_SUBSCRIBER_FAILED,
                escalation_id=escalation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="notify_dispatch_failed",
            )
