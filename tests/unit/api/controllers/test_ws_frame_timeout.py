"""Tests for the WebSocket per-frame idle timeout (DoS prevention).

The receive loop wraps every ``socket.receive_text()`` in
``asyncio.wait_for(..., timeout=ws_frame_timeout_seconds)``.  A
connected-but-silent client that holds a slot beyond the budget is
closed with policy code 1008 so a stalled peer cannot indefinitely
consume server resources.
"""

import asyncio

import pytest
from typeguard import suppress_type_checks

from synthorg.api.controllers.ws import _receive_loop
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole

pytestmark = pytest.mark.unit


def _make_auth_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="u-001",
        username="alice",
        role=HumanRole.CEO,
        auth_method=AuthMethod.JWT,
    )


class _SilentSocket:
    """Stand-in for a WebSocket that never sends a frame.

    ``receive_text()`` blocks on an :class:`asyncio.Event` that is
    never set so the wrapping ``asyncio.wait_for`` is the only path
    that can return.
    """

    def __init__(self) -> None:
        self._silent = asyncio.Event()
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.client = ("127.0.0.1", 1234)
        # ``_receive_loop`` reads ``socket.app.state["app_state"]`` to
        # resolve the timeout when not passed explicitly. The frame
        # timeout test passes ``frame_timeout_seconds=`` directly so
        # the app state is never accessed; provide a stub anyway in
        # case the implementation adds another lookup later.
        self.app = type("App", (), {"state": {"app_state": None}})()

    async def receive_text(self) -> str:
        await self._silent.wait()
        return ""

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason


async def test_receive_loop_closes_after_frame_timeout() -> None:
    """A silent client is closed with policy code 1008 after the budget.

    Also asserts the elapsed wall time is at least ``timeout * 0.9`` so
    a future refactor that accidentally halves or zeroes the timeout
    fails this test rather than passing on a near-zero close-code
    check.
    """
    timeout_seconds = 1
    socket = _SilentSocket()
    outbound: asyncio.Queue[bytes] = asyncio.Queue(maxsize=8)
    user = _make_auth_user()

    started = asyncio.get_event_loop().time()
    # ``socket`` is a behavioural ``_SilentSocket`` stand-in for a concrete
    # WebSocket; suppress the runtime check at the same boundary as the static
    # ``# type: ignore[arg-type]`` (the test verifies the idle-timeout close,
    # not socket type conformance).
    with suppress_type_checks():
        await _receive_loop(
            socket,  # type: ignore[arg-type]
            subscribed=set(),
            filters={},
            conn_user=user,
            outbound_queue=outbound,
            frame_timeout_seconds=timeout_seconds,
        )
    elapsed = asyncio.get_event_loop().time() - started

    assert socket.closed is True
    assert socket.close_code == 1008
    assert "frame timeout" in (socket.close_reason or "")
    # Allow a small floor (0.9x) for scheduler jitter; reject any
    # implementation that closes substantially earlier than the budget.
    assert elapsed >= timeout_seconds * 0.9, (
        f"loop closed too quickly: {elapsed:.3f}s < {timeout_seconds * 0.9:.3f}s"
    )
