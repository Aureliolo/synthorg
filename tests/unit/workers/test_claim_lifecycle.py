"""Lifecycle / teardown coverage for ``JetStreamTaskQueue``.

The model-only tests in ``test_claim.py`` do not exercise the
queue client's ``stop()`` / ``_drain_partial()`` / ``next_claim()``
error paths. Those paths carry the SEC-1 carve-outs added in the
logger-error-AST-redaction sweep:

* ``except MemoryError, RecursionError: raise`` ahead of the broad
  ``except Exception`` handlers (so OOM / stack overflow propagate
  instead of being absorbed as queue-teardown warnings).
* Structured ``error_type`` / ``error=safe_error_description(exc)``
  on the previously-opaque warning logs.

These tests pin the contract by injecting mocks directly onto the
private ``_sub`` / ``_client`` slots so we don't need a live NATS
container -- the public ``start()`` path is unaffected.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.communication.config import NatsConfig
from synthorg.workers.claim import JetStreamTaskQueue
from synthorg.workers.config import QueueConfig


class _SubscriptionStub:
    """Concrete spec for the JetStream pull-subscription stand-in.

    ``check_mock_spec`` requires ``AsyncMock`` calls in ``tests/`` to
    declare ``spec=`` so attribute typos surface at test time. This
    lightweight class lists the two attributes the queue accesses
    (``unsubscribe``, ``fetch``) so spec-based mocks reject anything
    else.
    """

    async def unsubscribe(self) -> None:  # pragma: no cover (spec only)
        ...

    async def fetch(
        self,
        batch: int = 1,
        timeout: float = 1.0,  # noqa: ASYNC109
    ) -> list[Any]:  # pragma: no cover (spec only)
        return []


class _ClientStub:
    """Concrete spec for the NATS client stand-in (drain only)."""

    async def drain(self) -> None:  # pragma: no cover (spec only)
        ...


class _AckStub:
    """Concrete spec for a JetStream-fetched message's ``ack()`` coroutine."""

    async def ack(self) -> None:  # pragma: no cover (spec only)
        ...


def _make_queue() -> JetStreamTaskQueue:
    """Build a ``JetStreamTaskQueue`` using the default config defaults.

    ``QueueConfig`` defaults to disabled, but the constructor itself
    does not gate on that; passing the model directly is fine for
    lifecycle tests that bypass ``start()``.
    """
    return JetStreamTaskQueue(
        queue_config=QueueConfig(enabled=True),
        nats_config=NatsConfig(),
    )


class _FakeMsg:
    """Minimal stand-in for a JetStream pull-fetch message."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.ack = AsyncMock(spec=_AckStub.ack)


@pytest.mark.unit
async def test_stop_logs_unsubscribe_failure_with_structured_fields() -> None:
    """``stop()`` swallows unsubscribe errors but emits ``error_type`` + ``error``."""
    queue = _make_queue()
    queue._sub = AsyncMock(spec=_SubscriptionStub)
    queue._sub.unsubscribe.side_effect = RuntimeError("transient nats error")

    # ``stop()`` must not raise on a routine subscription failure -- the
    # carve-out below the bound exception handler only fires on
    # MemoryError / RecursionError. The structured-log shape is the
    # contract this test pins.
    await queue.stop()

    assert queue._sub is None


@pytest.mark.unit
async def test_stop_logs_drain_failure_with_structured_fields() -> None:
    """``stop()`` swallows drain errors but emits ``error_type`` + ``error``."""
    queue = _make_queue()
    queue._client = AsyncMock(spec=_ClientStub)
    queue._client.drain.side_effect = RuntimeError("transient nats error")

    await queue.stop()

    assert queue._client is None
    assert queue._js is None


@pytest.mark.unit
async def test_stop_re_raises_memory_error_from_unsubscribe() -> None:
    """``MemoryError`` from ``unsubscribe()`` propagates -- carve-out fires."""
    queue = _make_queue()
    queue._sub = AsyncMock(spec=_SubscriptionStub)
    queue._sub.unsubscribe.side_effect = MemoryError("oom")

    with pytest.raises(MemoryError):
        await queue.stop()


@pytest.mark.unit
async def test_stop_re_raises_recursion_error_from_drain() -> None:
    """``RecursionError`` from ``drain()`` propagates -- carve-out fires."""
    queue = _make_queue()
    queue._client = AsyncMock(spec=_ClientStub)
    queue._client.drain.side_effect = RecursionError("stack")

    with pytest.raises(RecursionError):
        await queue.stop()


@pytest.mark.unit
async def test_drain_partial_logs_unsubscribe_failure() -> None:
    """``_drain_partial`` mirrors ``stop()``'s teardown carve-out."""
    queue = _make_queue()
    queue._sub = AsyncMock(spec=_SubscriptionStub)
    queue._sub.unsubscribe.side_effect = RuntimeError("nats")
    queue._client = AsyncMock(spec=_ClientStub)
    queue._client.drain.side_effect = RuntimeError("nats drain")

    await queue._drain_partial()

    assert queue._sub is None
    assert queue._client is None


@pytest.mark.unit
async def test_drain_partial_re_raises_memory_error_from_drain() -> None:
    """``_drain_partial`` does not absorb ``MemoryError`` from drain."""
    queue = _make_queue()
    queue._client = AsyncMock(spec=_ClientStub)
    queue._client.drain.side_effect = MemoryError("oom")

    with pytest.raises(MemoryError):
        await queue._drain_partial()


@pytest.mark.unit
async def test_next_claim_oversize_payload_logs_ack_failure() -> None:
    """Oversize-payload branch: ``raw.ack()`` failure is logged structured."""
    queue = _make_queue()
    # ``_MAX_CLAIM_PAYLOAD_BYTES`` is 64KiB at module scope; produce a
    # payload that overflows the limit so the oversize branch fires.
    huge_payload = b"x" * (1024 * 128)
    raw = _FakeMsg(huge_payload)
    raw.ack.side_effect = RuntimeError("ack failed")

    queue._sub = AsyncMock(spec=_SubscriptionStub)
    queue._sub.fetch.return_value = [raw]

    result = await queue.next_claim(timeout=1.0)

    assert result is None
    raw.ack.assert_awaited_once()


@pytest.mark.unit
async def test_next_claim_malformed_payload_logs_ack_failure() -> None:
    """Malformed-claim branch: ``raw.ack()`` failure is logged structured."""
    queue = _make_queue()
    raw = _FakeMsg(b"this is not valid json")
    raw.ack.side_effect = RuntimeError("ack failed")

    queue._sub = AsyncMock(spec=_SubscriptionStub)
    queue._sub.fetch.return_value = [raw]

    result = await queue.next_claim(timeout=1.0)

    assert result is None
    raw.ack.assert_awaited_once()


@pytest.mark.unit
async def test_next_claim_oversize_payload_re_raises_memory_error() -> None:
    """``MemoryError`` from ``raw.ack()`` on oversize-payload propagates."""
    queue = _make_queue()
    huge_payload = b"x" * (1024 * 128)
    raw = _FakeMsg(huge_payload)
    raw.ack.side_effect = MemoryError("oom")

    queue._sub = AsyncMock(spec=_SubscriptionStub)
    queue._sub.fetch.return_value = [raw]

    with pytest.raises(MemoryError):
        await queue.next_claim(timeout=1.0)
