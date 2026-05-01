"""Unit tests for ``JetStreamMessageBus.health_check``.

Exercises the four degraded-probe branches plus the happy path.
Tests avoid a real NATS connection by mutating the bus's internal
``_state`` directly -- the method only reads ``state.running`` and
``state.client`` attributes and awaits ``client.flush``.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.communication.bus.nats import JetStreamMessageBus
from synthorg.communication.config import MessageBusConfig, NatsConfig
from synthorg.communication.enums import MessageBusBackend
from synthorg.observability import safe_error_description
from synthorg.observability.events.communication import COMM_BUS_HEALTH_CHECK_FAILED


def _make_bus() -> JetStreamMessageBus:
    """Build a JetStreamMessageBus without connecting to NATS."""
    config = MessageBusConfig(
        backend=MessageBusBackend.NATS,
        nats=NatsConfig(url="nats://localhost:4222"),
    )
    return JetStreamMessageBus(config=config)


@pytest.mark.unit
class TestNatsBusHealthCheck:
    """Branch coverage for ``JetStreamMessageBus.health_check``."""

    async def test_returns_false_when_not_running(self) -> None:
        bus = _make_bus()
        bus._state.running = False
        assert await bus.health_check() is False

    async def test_returns_false_when_client_none(self) -> None:
        bus = _make_bus()
        bus._state.running = True
        bus._state.client = None
        assert await bus.health_check() is False

    async def test_returns_false_when_not_connected(self) -> None:
        bus = _make_bus()
        bus._state.running = True
        client = MagicMock()
        client.is_connected = False
        client.flush = AsyncMock()
        bus._state.client = client
        assert await bus.health_check() is False
        client.flush.assert_not_called()

    @pytest.mark.parametrize(
        "flush_exc",
        [
            TimeoutError("flush timed out"),
            ConnectionError("broker unreachable"),
            OSError("socket closed"),
            RuntimeError("client in bad state"),
        ],
        ids=["timeout", "connection", "os", "runtime"],
    )
    async def test_returns_false_and_logs_when_flush_raises(
        self,
        flush_exc: Exception,
    ) -> None:
        # Flush exceptions log ``error_type`` +
        # ``error=safe_error_description(exc)``; no ``exc_info=True``
        # because serialized traceback frame-locals can leak the
        # credentials embedded in the NATS connection URL. The
        # contract is exception-type-agnostic, so the assertion
        # holds across timeout / connection / OS / generic runtime
        # failures.
        bus = _make_bus()
        bus._state.running = True
        client = MagicMock()
        client.is_connected = True
        client.flush = AsyncMock(side_effect=flush_exc)
        bus._state.client = client
        with patch("synthorg.communication.bus.nats.logger") as mock_logger:
            result = await bus.health_check()
        assert result is False
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args.args[0] == COMM_BUS_HEALTH_CHECK_FAILED
        assert call_args.kwargs["phase"] == "flush"
        assert call_args.kwargs["error_type"] == type(flush_exc).__name__
        # Exact-match the scrubbed payload so a regression that re-emits
        # ``str(exc)`` (which can carry attacker-controlled bytes) would
        # flip the test, not just a non-empty-string check.
        assert call_args.kwargs["error"] == safe_error_description(flush_exc)
        assert "exc_info" not in call_args.kwargs

    async def test_returns_true_when_flush_succeeds(self) -> None:
        bus = _make_bus()
        bus._state.running = True
        client = MagicMock()
        client.is_connected = True
        client.flush = AsyncMock()
        bus._state.client = client
        assert await bus.health_check() is True
        client.flush.assert_awaited_once_with(timeout=2)

    async def test_memory_error_is_reraised(self) -> None:
        bus = _make_bus()
        bus._state.running = True
        client = MagicMock()
        client.is_connected = True
        client.flush = AsyncMock(side_effect=MemoryError())
        bus._state.client = client
        with pytest.raises(MemoryError):
            await bus.health_check()
