"""Drain-timeout test for the JetStream bus ``stop()``.

Verifies the canonical pattern (per ``docs/reference/lifecycle-sync.md``):
a ``client.drain()`` that exceeds ``stop_drain_timeout_seconds`` marks
the state unrestartable and raises :class:`BusStopTimeoutError`, and the
shielded drain is abandoned (not cancelled) so it can still complete in
the background.
"""

import asyncio

import pytest

from synthorg.communication.bus._nats_connection import stop
from synthorg.communication.bus._nats_state import create_state
from synthorg.communication.bus.errors import BusStopTimeoutError
from synthorg.communication.config import MessageBusConfig, NatsConfig

pytestmark = pytest.mark.unit


class _HangingClient:
    """Stand-in NATS client whose ``drain()`` blocks until released."""

    def __init__(self, release: asyncio.Event) -> None:
        self._release = release
        self.drained = False

    async def drain(self) -> None:
        await self._release.wait()
        self.drained = True


class TestNatsStopDrainTimeout:
    """Canonical drain-timeout behaviour for the bus stop path."""

    async def test_stop_drain_timeout_marks_unrestartable(self) -> None:
        state = create_state(MessageBusConfig(nats=NatsConfig()))
        state.running = True
        state.stop_drain_timeout_seconds = 0.05
        release = asyncio.Event()
        client = _HangingClient(release)
        state.client = client  # type: ignore[assignment]

        try:
            with pytest.raises(BusStopTimeoutError):
                await stop(state)
            assert state.stop_failed is True
            # Retained handles are released so the dead client does not leak.
            assert state.client is None
        finally:
            # Release the abandoned shielded drain so it completes instead
            # of leaking past the test as a pending task.
            release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        # The shielded drain was abandoned, not cancelled: it still ran.
        assert client.drained is True
