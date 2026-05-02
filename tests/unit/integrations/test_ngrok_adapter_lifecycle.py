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


def _fake_connect(_port: int, _proto: str) -> _FakeTunnel:
    return _FakeTunnel()


def _fake_disconnect(_url: Any) -> None:
    return None


class TestNgrokAdapterLifecycle:
    """Adapter must serialise concurrent start / stop calls."""

    async def test_double_start_raises(self) -> None:
        """A second start() while a tunnel is active raises RuntimeError."""
        adapter = NgrokAdapter()
        with (
            patch(
                "synthorg.integrations.tunnel.ngrok_adapter.ngrok.connect",
                _fake_connect,
            ),
            patch(
                "synthorg.integrations.tunnel.ngrok_adapter.ngrok.disconnect",
                _fake_disconnect,
            ),
        ):
            url = await adapter.start()
            assert url == "https://fake.ngrok.io"
            with pytest.raises(RuntimeError, match="already active"):
                await adapter.start()
            await adapter.stop()

    async def test_concurrent_starts_yield_one_tunnel(self) -> None:
        """Two simultaneous start() calls: exactly one wins."""
        adapter = NgrokAdapter()
        with (
            patch(
                "synthorg.integrations.tunnel.ngrok_adapter.ngrok.connect",
                _fake_connect,
            ),
            patch(
                "synthorg.integrations.tunnel.ngrok_adapter.ngrok.disconnect",
                _fake_disconnect,
            ),
        ):
            results = await asyncio.gather(
                adapter.start(),
                adapter.start(),
                return_exceptions=True,
            )
            successes = [r for r in results if isinstance(r, str)]
            errors = [r for r in results if isinstance(r, RuntimeError)]
            assert len(successes) == 1
            assert len(errors) == 1
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
