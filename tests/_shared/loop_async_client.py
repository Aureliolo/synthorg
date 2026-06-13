"""Portal-free async test client for Litestar apps.

litestar 2.22's ``AsyncTestClient`` drives the ASGI lifespan (and every
request) through an anyio ``BlockingPortal``: a background thread with
its own event loop. On Windows that multiplies ``socket.socketpair``
creation (one extra loop per client, on a second concurrent thread) and
trips CPython 122797 under xdist contention.

``LoopAsyncClient`` instead runs BOTH the lifespan and every request on
the caller's already-running event loop, via ``httpx.ASGITransport``. A
test therefore creates exactly one event loop (the pytest-asyncio
function loop) and zero portals, returning the API suite to the
single-threaded per-test-loop pattern the rest of the async unit tests
already use. Use it as an async context manager::

    async with LoopAsyncClient(app) as client:
        response = await client.get("/health")
        assert response.status_code == 200

The ASGI lifespan is driven with two queues on the current loop: the
client sends ``lifespan.startup`` / ``lifespan.shutdown`` and awaits the
app's ``*.complete`` acknowledgement, exactly as an ASGI server would.
"""

import asyncio
import contextlib
from types import TracebackType
from typing import Self, override

import httpx
from litestar import Litestar
from litestar.types import (
    LifeSpanReceiveMessage,
    LifeSpanScope,
    LifeSpanSendMessage,
    LifeSpanShutdownEvent,
    LifeSpanStartupEvent,
)

_BASE_URL = "http://testserver.local"
# Match litestar's ``TestClientTransport`` request scope so tests that
# assert on ``request.client.host`` observe the same value they did
# under the sync client.
_TEST_CLIENT_ADDR: tuple[str, int] = ("testclient", 50000)


class LoopAsyncClient(httpx.AsyncClient):
    """An ``httpx.AsyncClient`` that serves a Litestar app in-process.

    Runs the ASGI lifespan and all requests on the caller's running
    event loop, with no anyio ``BlockingPortal`` (hence no extra thread,
    event loop, or ``socket.socketpair``).
    """

    def __init__(self, app: Litestar) -> None:
        """Build a client bound to ``app``.

        Args:
            app: The Litestar application under test.
        """
        self.app = app
        super().__init__(
            transport=httpx.ASGITransport(
                app=app,  # type: ignore[arg-type]  # Litestar is ASGI-callable; httpx types scope as a broad MutableMapping
                raise_app_exceptions=True,
                client=_TEST_CLIENT_ADDR,
            ),
            base_url=_BASE_URL,
            headers={"user-agent": "testclient"},
            follow_redirects=True,
        )
        self._to_app: asyncio.Queue[LifeSpanReceiveMessage] = asyncio.Queue()
        self._from_app: asyncio.Queue[LifeSpanSendMessage] = asyncio.Queue()
        self._lifespan_task: asyncio.Task[None] | None = None

    async def _receive(self) -> LifeSpanReceiveMessage:
        return await self._to_app.get()

    async def _send(self, message: LifeSpanSendMessage) -> None:
        await self._from_app.put(message)

    async def _run_lifespan(self) -> None:
        scope: LifeSpanScope = {
            "type": "lifespan",
            "app": self.app,
            "asgi": {"spec_version": "2.0", "version": "3.0"},
        }
        await self.app(scope, self._receive, self._send)

    async def _await_app_message(self, *, phase: str) -> LifeSpanSendMessage:
        """Await the app's next lifespan message, racing the lifespan task.

        A plain ``self._from_app.get()`` blocks forever if the lifespan
        task exits (crash or cancellation) without emitting a terminal
        ``lifespan.*`` message; under the per-test timeout that becomes an
        opaque 30s hang and ``os.abort``. Racing the queue read against
        the task surfaces the task's failure as a ``RuntimeError`` instead.

        Args:
            phase: ``"startup"`` or ``"shutdown"`` for the error message.

        Returns:
            The message the app sent on the lifespan channel.
        """
        task = self._lifespan_task
        if task is None:
            msg = "lifespan task is not running"
            raise RuntimeError(msg)
        get_message: asyncio.Task[LifeSpanSendMessage] = asyncio.ensure_future(
            self._from_app.get(),
        )
        waiters = {get_message, task}
        try:
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if get_message in done:
                return get_message.result()
        finally:
            # Always retire the queue read: a cancellation propagating out
            # of ``asyncio.wait`` (e.g. the per-test timeout cancelling
            # ``__aenter__``) would otherwise leave ``get_message`` pending
            # and leak the task.
            get_message.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await get_message
        reason = f"ASGI lifespan task exited during {phase} without a message"
        if not task.cancelled() and task.exception() is not None:
            raise RuntimeError(reason) from task.exception()
        raise RuntimeError(reason)

    async def _discard_lifespan_task(self) -> None:
        """Retire the lifespan task on the teardown path (best effort).

        A still-running task is cancelled; the result of an already-done
        task is awaited so asyncio does not warn about an unretrieved
        exception. Any exception the task carries is intentionally NOT
        re-raised here: a genuine app-side lifespan crash is surfaced by
        ``__aexit__`` BEFORE this runs (when no test exception is in
        flight), and re-raising during cleanup would mask the caller's
        clearer error.
        """
        task = self._lifespan_task
        self._lifespan_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    @override
    async def __aenter__(self) -> Self:
        await super().__aenter__()
        self._lifespan_task = asyncio.create_task(
            self._run_lifespan(),
            name="loop-async-client-lifespan",
        )
        # Guard the awaits: a cancellation or error after
        # super().__aenter__() must not leak the half-entered httpx client
        # or the orphaned lifespan task.
        try:
            startup: LifeSpanStartupEvent = {"type": "lifespan.startup"}
            await self._to_app.put(startup)
            message = await self._await_app_message(phase="startup")
        except BaseException:
            await self._discard_lifespan_task()
            await super().__aexit__(None, None, None)
            raise
        if message["type"] != "lifespan.startup.complete":
            detail = ""
            if message["type"] == "lifespan.startup.failed":
                detail = message.get("message", "")
            await self._discard_lifespan_task()
            await super().__aexit__(None, None, None)
            reason = f"ASGI lifespan startup failed: {message['type']} {detail}"
            raise RuntimeError(reason.strip())
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        task = self._lifespan_task
        try:
            if task is not None and not task.done():
                shutdown: LifeSpanShutdownEvent = {"type": "lifespan.shutdown"}
                await self._to_app.put(shutdown)
                ack = await self._await_app_message(phase="shutdown")
                if ack["type"] != "lifespan.shutdown.complete":
                    detail = ""
                    if ack["type"] == "lifespan.shutdown.failed":
                        detail = ack.get("message", "")
                    reason = f"ASGI lifespan shutdown failed: {ack['type']} {detail}"
                    raise RuntimeError(reason.strip())
            elif (
                task is not None
                and exc_type is None
                and not task.cancelled()
                and task.exception() is not None
            ):
                # The lifespan task finished before shutdown was driven:
                # it crashed mid-test after startup.complete. Surface that
                # crash, but only when the test body did not itself raise,
                # so a real test failure is never masked.
                reason = "ASGI lifespan task crashed during the test"
                raise RuntimeError(reason) from task.exception()
        finally:
            await self._discard_lifespan_task()
            await super().__aexit__(exc_type, exc_value, traceback)
