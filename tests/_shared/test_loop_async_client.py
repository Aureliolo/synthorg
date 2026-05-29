"""Tests for the portal-free ``LoopAsyncClient`` lifespan driver.

The happy path is exercised indirectly by every migrated API test; these
tests pin the error paths that those never hit: a lifespan that fails
startup, fails shutdown, or dies WITHOUT emitting a terminal message
(which must surface as a ``RuntimeError`` rather than hang until the
per-test timeout fires).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from litestar import Litestar, get
from litestar.types import (
    LifeSpanReceiveMessage,
    LifeSpanScope,
    LifeSpanSendMessage,
)

from tests._shared import LoopAsyncClient

pytestmark = pytest.mark.unit

# Fail fast (well under the 30s per-test wall) if a handshake ever hangs,
# so a regression in the race logic surfaces as a clean TimeoutError here
# instead of an opaque worker abort.
_HANDSHAKE_TIMEOUT_SECONDS = 5.0


@get("/ping")
async def _ping() -> dict[str, str]:
    return {"status": "ok"}


async def test_serves_requests_and_exposes_app() -> None:
    """Happy path: lifespan runs, request served, ``.app`` is the app."""
    app = Litestar(route_handlers=[_ping])
    async with LoopAsyncClient(app) as client:
        assert client.app is app
        response = await client.get("/ping")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


async def test_startup_failure_raises_runtime_error() -> None:
    """An ``on_startup`` exception surfaces as a ``RuntimeError``."""

    async def _boom() -> None:
        msg = "startup boom"
        raise RuntimeError(msg)

    app = Litestar(route_handlers=[_ping], on_startup=[_boom])
    async with asyncio.timeout(_HANDSHAKE_TIMEOUT_SECONDS):
        with pytest.raises(RuntimeError, match="lifespan startup failed"):
            async with LoopAsyncClient(app):
                pass


async def test_shutdown_failure_raises_runtime_error() -> None:
    """An ``on_shutdown`` exception surfaces as a ``RuntimeError``."""

    async def _boom() -> None:
        msg = "shutdown boom"
        raise RuntimeError(msg)

    app = Litestar(route_handlers=[_ping], on_shutdown=[_boom])
    async with asyncio.timeout(_HANDSHAKE_TIMEOUT_SECONDS):
        with pytest.raises(RuntimeError, match="lifespan shutdown failed"):
            async with LoopAsyncClient(app):
                pass


async def _startup_then_die(
    scope: LifeSpanScope,
    receive: Callable[[], Awaitable[LifeSpanReceiveMessage]],
    send: Callable[[LifeSpanSendMessage], Awaitable[None]],
) -> None:
    """Minimal ASGI lifespan app: ack startup, then crash on shutdown.

    Crashes WITHOUT sending ``lifespan.shutdown.complete`` / ``.failed``,
    so the client must surface the task's death rather than block on the
    queue forever.
    """
    del scope  # lifespan-only stub; scope is always the lifespan scope
    await receive()  # the startup event
    complete: LifeSpanSendMessage = {"type": "lifespan.startup.complete"}
    await send(complete)
    await receive()  # the shutdown event
    msg = "lifespan crashed on shutdown without sending a message"
    raise RuntimeError(msg)


async def test_lifespan_crash_without_message_surfaces_not_hangs() -> None:
    """A lifespan task that exits without a terminal message must not hang.

    The race in ``_await_app_message`` turns the missing-message case into
    a ``RuntimeError`` instead of a 30s queue block + worker abort.
    """
    app = cast("Litestar", _startup_then_die)
    async with asyncio.timeout(_HANDSHAKE_TIMEOUT_SECONDS):
        with pytest.raises(RuntimeError, match="exited during shutdown without"):
            async with LoopAsyncClient(app):
                pass
