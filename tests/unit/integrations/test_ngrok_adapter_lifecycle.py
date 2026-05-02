"""Lifecycle tests for ``NgrokAdapter``.

Verifies the adapter holds its lifecycle lock across both ``start``
and ``stop`` so concurrent invocations cannot create or tear down two
tunnels under the single-tunnel invariant.
"""

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from synthorg.integrations.tunnel.ngrok_adapter import NgrokAdapter

pytestmark = pytest.mark.unit


class _FakeTunnel:
    def __init__(self, public_url: str = "https://fake.ngrok.io") -> None:
        self.public_url = public_url


def _fake_connect(_port: int, _proto: str, **_kwargs: Any) -> _FakeTunnel:
    # The real ngrok.connect now receives ``pyngrok_config=`` as a
    # keyword arg from the adapter; accept and ignore it in the fake.
    return _FakeTunnel()


def _fake_disconnect(_url: Any) -> None:
    return None


class TestNgrokAdapterLifecycle:
    """Adapter must serialise concurrent start / stop calls."""

    async def test_double_start_is_idempotent(self) -> None:
        """A second start() while active returns the existing URL."""
        adapter = NgrokAdapter()
        connect_calls: list[int] = []

        def _counting_connect(*args: Any, **kwargs: Any) -> _FakeTunnel:
            connect_calls.append(1)
            return _fake_connect(*args, **kwargs)

        with (
            patch(
                "synthorg.integrations.tunnel.ngrok_adapter.ngrok.connect",
                _counting_connect,
            ),
            patch(
                "synthorg.integrations.tunnel.ngrok_adapter.ngrok.disconnect",
                _fake_disconnect,
            ),
        ):
            first = await adapter.start()
            second = await adapter.start()
            assert first == "https://fake.ngrok.io"
            assert second == first
            # The second call must NOT invoke ngrok.connect a second
            # time -- idempotency means the existing tunnel is reused
            # rather than a fresh one being negotiated upstream.
            assert len(connect_calls) == 1
            await adapter.stop()

    async def test_concurrent_starts_yield_one_tunnel(self) -> None:
        """Two simultaneous start() calls connect once and return the same URL."""
        adapter = NgrokAdapter()
        connect_calls: list[int] = []

        def _counting_connect(*args: Any, **kwargs: Any) -> _FakeTunnel:
            connect_calls.append(1)
            return _fake_connect(*args, **kwargs)

        with (
            patch(
                "synthorg.integrations.tunnel.ngrok_adapter.ngrok.connect",
                _counting_connect,
            ),
            patch(
                "synthorg.integrations.tunnel.ngrok_adapter.ngrok.disconnect",
                _fake_disconnect,
            ),
        ):
            results = list(
                await asyncio.gather(adapter.start(), adapter.start()),
            )
            assert results == ["https://fake.ngrok.io", "https://fake.ngrok.io"]
            assert len(connect_calls) == 1
            await adapter.stop()

    async def test_stop_without_start_is_noop(self) -> None:
        """stop() before any start() returns cleanly without disconnecting."""
        adapter = NgrokAdapter()
        with patch(
            "synthorg.integrations.tunnel.ngrok_adapter.ngrok.disconnect",
            _fake_disconnect,
        ):
            await adapter.stop()  # Must not raise.
        assert adapter._tunnel is None
