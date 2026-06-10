"""Tests for the ``ws_auth_timeout_seconds`` kill-switch wiring.

The WebSocket first-message auth handler reads the timeout from
``app_state.ws_auth_limits.auth_timeout_seconds``, which is baked in at
startup by ``_apply_bridge_config`` from the ``api.ws_auth_timeout_seconds``
setting.  Flipping the value on ``app_state`` must change the
effective timeout the handler passes to ``asyncio.wait_for``.

The two "is this read from app_state?" assertions monkeypatch
``asyncio.wait_for`` to observe the timeout value the handler
actually passes -- no wall-clock races.
"""

import asyncio
from types import SimpleNamespace

import pytest
from typeguard import suppress_type_checks

from synthorg.api.controllers.ws import _WS_CLOSE_AUTH_FAILED, _read_auth_message


async def _read_auth(socket: _NeverResolvingSocket) -> str | None:
    """Drive ``_read_auth_message`` with a behavioural ``_NeverResolvingSocket``.

    ``socket`` is a hang-forever stand-in for a concrete ``WebSocket``; the
    runtime check is suppressed at the same boundary as the static
    ``# type: ignore[arg-type]`` these calls carried, because the tests verify
    the timeout/close path, not socket type conformance.
    """
    with suppress_type_checks():
        return await _read_auth_message(socket)  # type: ignore[arg-type]  # behavioural socket stub, not a real WebSocket


class _NeverResolvingSocket:
    """Minimal ``WebSocket`` stand-in whose ``receive_text`` never returns.

    Enough surface for ``_read_auth_message`` to exercise the timeout
    path: it pulls ``app.state["app_state"]`` for the timeout, awaits
    ``receive_text()``, and on ``TimeoutError`` calls ``close(code, reason)``.
    """

    def __init__(self, *, ws_auth_timeout_seconds: float) -> None:
        self.closed_with: tuple[int, str] | None = None
        # Build a Litestar-shaped ``app.state`` dict accessor: the WS
        # handler reads ``socket.app.state["app_state"]``, so ``state``
        # must be subscriptable.
        state: dict[str, object] = {
            "app_state": SimpleNamespace(
                ws_auth_limits=SimpleNamespace(
                    auth_timeout_seconds=ws_auth_timeout_seconds,
                ),
            ),
        }
        self.app = SimpleNamespace(state=state)

    async def receive_text(self) -> str:
        """Block until cancelled -- exercises the ``asyncio.wait_for`` path."""
        await asyncio.Event().wait()
        return ""  # pragma: no cover -- unreachable

    async def close(self, *, code: int, reason: str) -> None:
        self.closed_with = (code, reason)


@pytest.mark.unit
class TestWsAuthTimeoutKillSwitch:
    """The configured timeout gates first-message auth behavior."""

    async def test_timeout_expiry_closes_socket_with_auth_failed_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A fired timeout closes the socket with the auth-timeout code.

        Deterministic: ``asyncio.wait_for`` is stubbed to raise
        ``TimeoutError`` immediately so the handler's timeout branch
        runs without racing a real clock.
        """
        socket = _NeverResolvingSocket(ws_auth_timeout_seconds=5.0)
        seen: dict[str, float] = {}

        async def _fake_wait_for(awaitable: object, timeout: float) -> str:  # noqa: ASYNC109 -- signature mirrors asyncio.wait_for
            seen["timeout"] = timeout
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise TimeoutError

        monkeypatch.setattr(
            "synthorg.api.controllers.ws.asyncio.wait_for",
            _fake_wait_for,
        )

        result = await _read_auth(socket)

        assert result is None
        assert seen["timeout"] == pytest.approx(5.0)
        assert socket.closed_with is not None
        code, _reason = socket.closed_with
        assert code == _WS_CLOSE_AUTH_FAILED

    async def test_timeout_read_from_app_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Handler passes ``ws_auth_limits.auth_timeout_seconds`` to ``wait_for``."""
        socket = _NeverResolvingSocket(ws_auth_timeout_seconds=100.0)
        seen: dict[str, float] = {}

        async def _fake_wait_for(awaitable: object, timeout: float) -> str:  # noqa: ASYNC109 -- signature mirrors asyncio.wait_for
            seen["timeout"] = timeout
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            return '{"action":"auth","ticket":"t"}'

        monkeypatch.setattr(
            "synthorg.api.controllers.ws.asyncio.wait_for",
            _fake_wait_for,
        )

        result = await _read_auth(socket)

        # The inner timeout (100s) was honored: the handler passed the
        # app_state value straight into wait_for, not a hardcoded number.
        assert seen["timeout"] == pytest.approx(100.0)
        assert socket.closed_with is None
        # ``_read_auth_message`` returns the extracted ticket, not the
        # raw JSON frame.
        assert result == "t"

    async def test_timeout_rebound_before_handler_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mutating ``ws_auth_limits.auth_timeout_seconds`` before entry takes effect.

        Confirms the handler does not cache the timeout behind its own
        module constant -- the in-flight value on ``app_state`` is the
        value actually passed to ``wait_for`` at entry.
        """
        socket = _NeverResolvingSocket(ws_auth_timeout_seconds=10.0)
        socket.app.state["app_state"].ws_auth_limits.auth_timeout_seconds = 42.0
        seen: dict[str, float] = {}

        async def _fake_wait_for(awaitable: object, timeout: float) -> str:  # noqa: ASYNC109 -- signature mirrors asyncio.wait_for
            seen["timeout"] = timeout
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise TimeoutError

        monkeypatch.setattr(
            "synthorg.api.controllers.ws.asyncio.wait_for",
            _fake_wait_for,
        )

        await _read_auth(socket)

        assert seen["timeout"] == pytest.approx(42.0)
