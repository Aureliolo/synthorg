"""Unit tests for ``RequestDrainMiddleware`` (#1600 Phase 3).

Pins the contract:

1. New requests pass through normally before ``begin_drain`` runs.
2. New requests after ``begin_drain`` are short-circuited with 503 +
   ``Retry-After: 5``.
3. ``begin_drain`` waits for in-flight requests to complete (up to
   the configured budget).
4. A drain that times out logs ``API_APP_DRAIN_TIMEOUT`` and
   returns so the caller (the on_shutdown hook) can proceed with
   service teardown.
5. Lifespan and WebSocket scopes pass through untouched.
6. The timeout bound is enforced (positive only).
"""

# The tests below use loosely-typed ASGI stubs that don't conform to
# Litestar's strict ``ASGIApp`` / ``Scope`` / ``Receive`` / ``Send``
# unions; that is intentional (the middleware contract is the ASGI
# spec, not the Litestar type overlay) and the runtime behaviour is
# correct. mypy's ``arg-type`` error fires once per call site, so
# silence it file-wide rather than littering every line.
# mypy: disable-error-code=arg-type

import asyncio
from typing import Any

import pytest
import structlog.testing

from synthorg.api.drain import RequestDrainMiddleware
from synthorg.observability.events.api import (
    API_APP_DRAIN_COMPLETED,
    API_APP_DRAIN_STARTED,
    API_APP_DRAIN_TIMEOUT,
)


async def _ok_app(
    scope: dict[str, Any],
    receive: Any,
    send: Any,
) -> None:
    """Trivial inner ASGI app: returns 200 for HTTP, no-op otherwise."""
    if scope["type"] != "http":
        return
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        },
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"ok",
            "more_body": False,
        },
    )


async def _slow_app(
    scope: dict[str, Any],
    receive: Any,
    send: Any,
    *,
    delay_s: float,
) -> None:
    """Inner app that sleeps before responding (test in-flight requests)."""
    if scope["type"] != "http":
        return
    await asyncio.sleep(delay_s)
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        },
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"slow",
            "more_body": False,
        },
    )


class _Recorder:
    """Collects ASGI ``send`` messages for assertions."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def __call__(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


async def _empty_receive() -> dict[str, Any]:  # pragma: no cover - never called
    return {"type": "http.disconnect"}


def _http_scope(path: str = "/") -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "server": ("test", 80),
        "client": ("test-client", 1234),
    }


@pytest.mark.unit
class TestRequestDrainMiddleware:
    """Drain middleware contract."""

    async def test_request_lands_normally_before_drain(self) -> None:
        mw = RequestDrainMiddleware(_ok_app, drain_timeout_seconds=5.0)
        recorder = _Recorder()
        await mw(_http_scope(), _empty_receive, recorder)
        assert recorder.messages[0]["status"] == 200
        assert recorder.messages[1]["body"] == b"ok"
        assert mw.inflight == 0
        assert not mw.drain_started

    async def test_new_request_after_drain_returns_503(self) -> None:
        mw = RequestDrainMiddleware(_ok_app, drain_timeout_seconds=5.0)
        await mw.begin_drain()
        recorder = _Recorder()
        await mw(_http_scope(), _empty_receive, recorder)
        assert recorder.messages[0]["status"] == 503
        retry_after = dict(recorder.messages[0]["headers"]).get(b"retry-after")
        assert retry_after == b"5"
        assert mw.drain_started

    async def test_inflight_request_completes_before_drain_returns(
        self,
    ) -> None:
        async def app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            await _slow_app(scope, receive, send, delay_s=0.05)

        mw = RequestDrainMiddleware(app, drain_timeout_seconds=5.0)
        recorder = _Recorder()
        request_task = asyncio.create_task(
            mw(_http_scope(), _empty_receive, recorder),
        )
        # Yield to let the request enter the in-flight section.
        await asyncio.sleep(0)
        assert mw.inflight == 1
        with structlog.testing.capture_logs() as logs:
            await mw.begin_drain()
        assert mw.inflight == 0
        events = [log["event"] for log in logs]
        assert API_APP_DRAIN_STARTED in events
        assert API_APP_DRAIN_COMPLETED in events
        await request_task
        assert recorder.messages[0]["status"] == 200

    async def test_drain_timeout_logged_when_inflight_exceeds_budget(
        self,
    ) -> None:
        async def app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            await _slow_app(scope, receive, send, delay_s=0.5)

        mw = RequestDrainMiddleware(app, drain_timeout_seconds=0.05)
        recorder = _Recorder()
        request_task = asyncio.create_task(
            mw(_http_scope(), _empty_receive, recorder),
        )
        await asyncio.sleep(0)
        with structlog.testing.capture_logs() as logs:
            await mw.begin_drain()
        events = [log["event"] for log in logs]
        assert API_APP_DRAIN_TIMEOUT in events
        # Drain returned even though the request is still in flight;
        # finish it so the test does not leak a pending task.
        await request_task

    async def test_lifespan_scope_passes_through(self) -> None:
        captured: list[dict[str, Any]] = []

        async def app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            captured.append(scope)

        mw = RequestDrainMiddleware(app, drain_timeout_seconds=1.0)
        # Drain has started; lifespan must still route to the app.
        await mw.begin_drain()
        await mw(
            {"type": "lifespan"},
            _empty_receive,
            _Recorder(),
        )
        assert captured == [{"type": "lifespan"}]

    async def test_websocket_scope_passes_through(self) -> None:
        captured: list[dict[str, Any]] = []

        async def app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            captured.append(scope)

        mw = RequestDrainMiddleware(app, drain_timeout_seconds=1.0)
        await mw.begin_drain()
        await mw(
            {"type": "websocket"},
            _empty_receive,
            _Recorder(),
        )
        assert captured == [{"type": "websocket"}]

    async def test_double_begin_drain_is_idempotent(self) -> None:
        mw = RequestDrainMiddleware(_ok_app, drain_timeout_seconds=5.0)
        await mw.begin_drain()
        # Second call returns immediately (already-started fast path).
        await mw.begin_drain()
        assert mw.drain_started

    def test_invalid_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="must be > 0"):
            RequestDrainMiddleware(_ok_app, drain_timeout_seconds=0)
        with pytest.raises(ValueError, match="must be > 0"):
            RequestDrainMiddleware(_ok_app, drain_timeout_seconds=-1.0)

    async def test_lifespan_shutdown_drains_before_inner_app_sees_message(
        self,
    ) -> None:
        """``begin_drain`` must run before the inner Litestar app sees shutdown.

        The inner Litestar app's ``on_shutdown`` hooks kick off the
        per-service teardown; they must only fire after in-flight HTTP
        requests have finished, so the drain has to land before the
        shutdown message reaches the inner app.
        """
        order: list[str] = []

        async def inner_app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            if scope["type"] != "lifespan":
                return
            message = await receive()
            order.append(f"app-saw:{message['type']}")

        class _Tracked(RequestDrainMiddleware):
            async def begin_drain(self) -> None:  # type: ignore[override]
                order.append("begin_drain")
                await super().begin_drain()

        async def fake_receive() -> dict[str, Any]:
            return {"type": "lifespan.shutdown"}

        tracked = _Tracked(inner_app, drain_timeout_seconds=1.0)
        await tracked({"type": "lifespan"}, fake_receive, _Recorder())
        assert order == ["begin_drain", "app-saw:lifespan.shutdown"]

    async def test_inflight_decremented_when_inner_app_raises(self) -> None:
        """The finally block must run even if the inner app raises."""

        async def failing_app(
            scope: dict[str, Any],
            receive: Any,
            send: Any,
        ) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        mw = RequestDrainMiddleware(failing_app, drain_timeout_seconds=1.0)
        recorder = _Recorder()
        with pytest.raises(RuntimeError, match="boom"):
            await mw(_http_scope(), _empty_receive, recorder)
        assert mw.inflight == 0
        # The drain can still complete promptly because no work is
        # outstanding.
        await mw.begin_drain()
