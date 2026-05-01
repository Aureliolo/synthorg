"""Tests for ``JetStreamMessageBus.health_check`` log redaction.

Flush exceptions on the health probe path must log at WARNING with
``error_type`` and a scrubbed ``error`` description -- never a raw
traceback (``exc_info=True``) -- because the NATS connection URL
can legitimately carry credentials and serialized frame-locals are
a known leak vector.
"""

# mypy: disable-error-code="union-attr,method-assign"

from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from synthorg.communication.bus.nats import JetStreamMessageBus
from synthorg.communication.config import (
    MessageBusConfig,
    MessageRetentionConfig,
    NatsConfig,
)
from synthorg.communication.enums import MessageBusBackend

pytestmark = pytest.mark.unit


def _build_bus() -> JetStreamMessageBus:
    config = MessageBusConfig(
        backend=MessageBusBackend.NATS,
        channels=("#test",),
        retention=MessageRetentionConfig(max_messages_per_channel=100),
        nats=NatsConfig(
            url="nats://operator:super-secret-token@localhost:4222",
            stream_name_prefix="TEST",
            publish_ack_wait_seconds=5.0,
            health_flush_timeout_seconds=2.0,
        ),
    )
    bus = JetStreamMessageBus(config=config)
    # Mark the bus running and mount a connected-looking mock client
    # so ``health_check`` reaches the ``flush`` call site.
    bus._state.running = True
    client = MagicMock()
    client.is_connected = True
    client.flush = AsyncMock()
    bus._state.client = client
    return bus


class TestHealthCheckSec1Logging:
    """``health_check`` flush failures log sanitised diagnostics."""

    async def test_flush_failure_logs_safe_description(self) -> None:
        """The warning carries ``error_type`` + ``error`` but no traceback frames."""
        bus = _build_bus()
        bus._state.client.flush = AsyncMock(
            side_effect=RuntimeError("broker unreachable"),
        )

        with structlog.testing.capture_logs() as logs:
            result = await bus.health_check()

        assert result is False
        probe_logs = [
            log
            for log in logs
            if log.get("event") == "communication.bus.health_check_failed"
        ]
        assert len(probe_logs) == 1
        log = probe_logs[0]
        assert log["log_level"] == "warning"
        assert log["phase"] == "flush"
        assert log["error_type"] == "RuntimeError"
        # The log entry must carry a redacted description, not a
        # raw traceback. ``exc_info`` would surface frame-locals
        # that can include the NATS URL's user:pass component.
        assert isinstance(log["error"], str)
        assert log["error"]
        assert "Traceback" not in log["error"]
        assert "exc_info" not in log
        # Belt-and-braces: the credential fragments from the test
        # config's connection URL must never reach the log record.
        assert "super-secret-token" not in log["error"]
        assert "operator:super-secret-token" not in log["error"]
        assert "operator:" not in log["error"]

    async def test_healthy_connection_returns_true(self) -> None:
        """Happy path: flush succeeds, ``True`` returned, no warning."""
        bus = _build_bus()

        with structlog.testing.capture_logs() as logs:
            result = await bus.health_check()

        assert result is True
        assert not any(
            log.get("event") == "communication.bus.health_check_failed" for log in logs
        )

    async def test_not_running_returns_false_without_probe(self) -> None:
        """Non-running bus returns ``False`` without invoking ``flush``."""
        bus = _build_bus()
        bus._state.running = False

        result = await bus.health_check()

        assert result is False
        bus._state.client.flush.assert_not_awaited()
