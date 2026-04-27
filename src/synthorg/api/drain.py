"""HTTP request-drain middleware (#1600).

Purpose: when the Litestar app receives a shutdown signal, stop
accepting new HTTP requests and let in-flight requests finish (up
to a configured budget) before the service-teardown hooks run.
Without this layer, long-running endpoints get cancelled
mid-transaction by uvicorn's lifespan close, and new requests
that race the shutdown signal land on services that are partway
torn down.

Operates as a raw ASGI middleware (not a Litestar
``MiddlewareProtocol``) so it sees the full ``scope`` and can
short-circuit non-HTTP scopes (lifespan / WebSocket) without
involving Litestar's request layer.

Lifecycle:

* Construction wires the inner ``app`` and stores the drain
  budget. ``_drain_started`` is an ``asyncio.Event`` flipped by
  ``begin_drain``; ``_idle`` is an event that mirrors
  "no in-flight requests" so ``begin_drain`` can wait on it.
* Each HTTP request that arrives **before** ``begin_drain`` runs
  increments ``_inflight``, runs the inner app, then decrements.
  When the counter reaches zero, ``_idle`` is set so a waiting
  ``begin_drain`` can return.
* Each HTTP request that arrives **after** ``begin_drain`` is
  rejected with ``503 Service Unavailable`` + ``Retry-After: 5``.
* ``begin_drain`` flips the started flag and waits up to the drain
  budget for the in-flight count to reach zero.

The middleware is instance-based: a single object is shared
across all requests. The inflight counter and ``_idle`` event
are mutated only between ``await`` points, so on the
single-threaded asyncio loop they are atomic relative to other
coroutines without needing a lock. Concretely: the request
path runs ``check drain gate -> inc inflight -> clear idle ->
await app``, with no ``await`` between the gate check and the
counter mutation. Another task (``begin_drain``) cannot
interleave between any of those steps -- coroutines only yield
at ``await`` points -- so a request cannot pass the gate, see
the drain start mid-flight, and silently slip past teardown.
Tests cover normal completion, the 503 short-circuit, and the
drain-timeout fallback.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APP_DRAIN_COMPLETED,
    API_APP_DRAIN_SEND_FAILED,
    API_APP_DRAIN_STARTED,
    API_APP_DRAIN_TIMEOUT,
)

if TYPE_CHECKING:
    from litestar.types import ASGIApp, Receive, Scope, Send

logger = get_logger(__name__)


_DRAIN_RESPONSE_BODY: bytes = b'{"status_code":503,"detail":"Service is shutting down"}'


class RequestDrainMiddleware:
    """ASGI middleware that drains in-flight requests on shutdown.

    Args:
        app: The wrapped ASGI app (the Litestar instance).
        drain_timeout_seconds: Maximum seconds to wait for in-flight
            requests to complete after :meth:`begin_drain`. Must be
            positive.

    Raises:
        ValueError: If ``drain_timeout_seconds`` is not positive.
    """

    __slots__ = (
        "_app",
        "_drain_started",
        "_drain_timeout",
        "_idle",
        "_inflight",
    )

    def __init__(
        self,
        app: ASGIApp,
        *,
        drain_timeout_seconds: float,
    ) -> None:
        if drain_timeout_seconds <= 0:
            msg = f"drain_timeout_seconds must be > 0, got {drain_timeout_seconds}"
            raise ValueError(msg)
        self._app = app
        self._drain_started = asyncio.Event()
        self._inflight = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._drain_timeout = drain_timeout_seconds

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Wrap each request with drain-aware bookkeeping.

        For HTTP scopes, the in-flight counter is updated and the
        drain gate enforced. For lifespan scopes, the receive
        callback is wrapped so a ``lifespan.shutdown`` message
        triggers :meth:`begin_drain` *before* the inner app sees
        the shutdown event -- this means the drain runs first, then
        the inner Litestar app starts its ``on_shutdown`` hooks
        (which kick off the per-service teardown). WebSocket scopes
        pass through untouched.
        """
        # Litestar's ``Scope`` union is ``HTTPScope | WebSocketScope``;
        # at runtime ASGI also delivers ``lifespan`` scopes which
        # the static type does not include. The ``type: ignore``s
        # below cover the lifespan branch.
        scope_type = scope["type"]
        if scope_type == "lifespan":  # type: ignore[comparison-overlap]
            await self._app(scope, self._wrap_lifespan_receive(receive), send)
            return
        if scope_type != "http":  # type: ignore[comparison-overlap]
            await self._app(scope, receive, send)
            return
        if self._drain_started.is_set():
            await _send_drain_response(send)
            return
        # Single-threaded asyncio: counter + event ops have no
        # ``await`` between them, so they are atomic relative to
        # other coroutines and need no lock.
        self._inflight += 1
        self._idle.clear()
        try:
            await self._app(scope, receive, send)
        finally:
            self._inflight -= 1
            if self._inflight == 0:
                self._idle.set()

    def _wrap_lifespan_receive(self, receive: Receive) -> Receive:
        """Wrap the lifespan receive so ``lifespan.shutdown`` triggers drain.

        The wrapped callable awaits the next lifespan message; if
        it is the ``shutdown`` event it runs :meth:`begin_drain`
        first, then forwards the message. The inner Litestar app
        thus only sees the shutdown event after the drain has
        completed (or timed out), guaranteeing service teardown
        runs after in-flight requests finish.
        """

        async def _receive() -> object:
            message = await receive()
            # ASGI lifespan messages are dicts with a ``type`` field;
            # the inner ``Receive`` type's union does not include
            # ``lifespan.shutdown`` so mypy flags the comparison
            # overlap, but at runtime the message is exactly that
            # ASGI event.
            if (
                isinstance(message, dict) and message.get("type") == "lifespan.shutdown"  # type: ignore[comparison-overlap]
            ):
                await self.begin_drain()
            return message

        return _receive  # type: ignore[return-value]

    async def begin_drain(self) -> None:
        """Stop accepting new requests and wait for in-flight to drain.

        Idempotent: a second call after the drain has started is a
        no-op (the second waiter will see ``_idle`` already set if
        the first drain completed). Logs:

        * ``API_APP_DRAIN_STARTED`` when the gate flips.
        * ``API_APP_DRAIN_COMPLETED`` when ``_inflight`` reaches zero.
        * ``API_APP_DRAIN_TIMEOUT`` if the budget elapses with
          requests still in flight; ``begin_drain`` returns so
          downstream service teardown can proceed.
        """
        already_started = self._drain_started.is_set()
        self._drain_started.set()
        if already_started:
            return
        logger.info(
            API_APP_DRAIN_STARTED,
            inflight=self._inflight,
            timeout_seconds=self._drain_timeout,
        )
        try:
            await asyncio.wait_for(
                self._idle.wait(),
                timeout=self._drain_timeout,
            )
        except TimeoutError:
            logger.warning(
                API_APP_DRAIN_TIMEOUT,
                inflight=self._inflight,
                timeout_seconds=self._drain_timeout,
            )
            return
        logger.info(API_APP_DRAIN_COMPLETED, inflight=self._inflight)

    @property
    def inflight(self) -> int:
        """Current in-flight HTTP request count (test introspection)."""
        return self._inflight

    @property
    def drain_started(self) -> bool:
        """True once :meth:`begin_drain` has been invoked."""
        return self._drain_started.is_set()


async def _send_drain_response(send: Send) -> None:
    """Send a 503 with ``Retry-After: 5`` to a request after drain start.

    Client disconnects during shutdown are common; the ASGI ``send``
    callable may raise if the peer is already gone. Swallow these
    errors so they do not crash the lifespan layer; ``MemoryError``
    and ``RecursionError`` always propagate.
    """
    try:
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_DRAIN_RESPONSE_BODY)).encode()),
                    (b"retry-after", b"5"),
                ],
            },
        )
        await send(
            {
                "type": "http.response.body",
                "body": _DRAIN_RESPONSE_BODY,
                "more_body": False,
            },
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.debug(
            API_APP_DRAIN_SEND_FAILED,
            error_type=type(exc).__name__,
        )
