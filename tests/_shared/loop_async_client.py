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

    def __init__(
        self,
        app: Litestar,
        *,
        raise_app_exceptions: bool = True,
    ) -> None:
        """Build a client bound to ``app``.

        Args:
            app: The Litestar application under test.
            raise_app_exceptions: When True (the default, matching
                litestar's clients) an unhandled exception in the app
                propagates out of the request call instead of being
                wrapped in a 500 response.
        """
        self.app = app
        super().__init__(
            transport=httpx.ASGITransport(
                app=app,  # type: ignore[arg-type] -- Litestar is ASGI-callable; httpx types scope as a broad MutableMapping
                raise_app_exceptions=raise_app_exceptions,
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

    async def _discard_lifespan_task(self) -> None:
        """Retire the lifespan task on the error/teardown path.

        Any exception the task itself carries is the same failure
        already surfaced by the caller from the ASGI ``lifespan.*.failed``
        message, so it is intentionally not re-raised here (doing so
        would mask the clearer ``RuntimeError`` the caller raises).
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
        startup: LifeSpanStartupEvent = {"type": "lifespan.startup"}
        await self._to_app.put(startup)
        message = await self._from_app.get()
        if message["type"] != "lifespan.startup.complete":
            detail = ""
            if message["type"] == "lifespan.startup.failed":
                detail = message["message"]
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
        try:
            task = self._lifespan_task
            if task is not None and not task.done():
                shutdown: LifeSpanShutdownEvent = {"type": "lifespan.shutdown"}
                await self._to_app.put(shutdown)
                ack = await self._from_app.get()
                if ack["type"] != "lifespan.shutdown.complete":
                    detail = ""
                    if ack["type"] == "lifespan.shutdown.failed":
                        detail = ack["message"]
                    reason = f"ASGI lifespan shutdown failed: {ack['type']} {detail}"
                    raise RuntimeError(reason.strip())
                await task
                self._lifespan_task = None
        finally:
            await self._discard_lifespan_task()
            await super().__aexit__(exc_type, exc_value, traceback)
