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
from unittest.mock import AsyncMock, MagicMock

import pytest

import synthorg.workers.claim as claim_module
from synthorg.communication.config import NatsConfig
from synthorg.workers.claim import _MAX_CLAIM_PAYLOAD_BYTES, JetStreamTaskQueue
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


class _AckCallableStub:
    """Concrete callable spec for a message's ``ack()`` coroutine.

    ``AsyncMock(spec=...)`` rejects unbound-method specs (``_AckStub.ack``)
    because ``mock`` resolves the spec via attribute access on the
    target. Spec-ing on a class with ``async def __call__`` gives a
    well-typed callable contract that mocks accept cleanly.
    """

    async def __call__(
        self, *args: object, **kwargs: object
    ) -> None:  # pragma: no cover
        ...


class _LoggerStub:
    """Concrete spec for the structlog-bound logger used by the queue.

    The queue only invokes ``warning`` / ``error`` / ``info`` / ``debug``
    (structlog ``BoundLoggerLazyProxy`` exposes these as bound methods).
    Listing them explicitly here means the spy mock rejects unexpected
    attribute access at test time -- a typo in a future
    ``logger.something_else(...)`` call would surface as
    ``AttributeError`` instead of being silently absorbed.
    """

    def warning(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        ...

    def error(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        ...

    def info(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
        ...

    def debug(self, *args: object, **kwargs: object) -> None:  # pragma: no cover
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
        self.ack = AsyncMock(spec=_AckCallableStub)


def _patch_logger(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the queue module's logger with a spy and return it.

    structlog's ``BoundLoggerLazyProxy`` caches bound severity methods
    on the proxy's ``__dict__`` after first access; assigning a new
    ``MagicMock`` over the proxy bypasses that cache because the proxy
    object itself is replaced. The fixture-style helper centralises
    this pattern across the tests below.
    """
    spy = MagicMock(spec=_LoggerStub)
    monkeypatch.setattr(claim_module, "logger", spy)
    return spy


@pytest.mark.unit
async def test_stop_logs_unsubscribe_failure_with_structured_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stop()`` swallows unsubscribe errors but emits ``error_type`` + ``error``."""
    spy = _patch_logger(monkeypatch)
    queue = _make_queue()
    queue._sub = AsyncMock(spec=_SubscriptionStub)
    queue._sub.unsubscribe.side_effect = RuntimeError("transient nats error")

    # ``stop()`` must not raise on a routine subscription failure -- the
    # carve-out below the bound exception handler only fires on
    # MemoryError / RecursionError. The structured-log shape is the
    # contract this test pins.
    await queue.stop()

    assert queue._sub is None
    spy.warning.assert_called_once()
    kwargs = spy.warning.call_args.kwargs
    assert kwargs["error_type"] == "RuntimeError"
    assert kwargs["error"].startswith("RuntimeError:")


@pytest.mark.unit
async def test_stop_logs_drain_failure_with_structured_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stop()`` swallows drain errors but emits ``error_type`` + ``error``."""
    spy = _patch_logger(monkeypatch)
    queue = _make_queue()
    queue._client = AsyncMock(spec=_ClientStub)
    queue._client.drain.side_effect = RuntimeError("transient nats error")

    await queue.stop()

    assert queue._client is None
    assert queue._js is None
    spy.warning.assert_called_once()
    kwargs = spy.warning.call_args.kwargs
    assert kwargs["error_type"] == "RuntimeError"
    assert kwargs["error"].startswith("RuntimeError:")


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
async def test_drain_partial_logs_unsubscribe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_drain_partial`` mirrors ``stop()``'s teardown carve-out."""
    spy = _patch_logger(monkeypatch)
    queue = _make_queue()
    queue._sub = AsyncMock(spec=_SubscriptionStub)
    queue._sub.unsubscribe.side_effect = RuntimeError("nats")
    queue._client = AsyncMock(spec=_ClientStub)
    queue._client.drain.side_effect = RuntimeError("nats drain")

    await queue._drain_partial()

    assert queue._sub is None
    assert queue._client is None
    # Both unsubscribe and drain failed -- two structured warnings.
    assert spy.warning.call_count == 2
    for call in spy.warning.call_args_list:
        assert call.kwargs["error_type"] == "RuntimeError"
        assert call.kwargs["error"].startswith("RuntimeError:")


@pytest.mark.unit
async def test_drain_partial_re_raises_memory_error_from_drain() -> None:
    """``_drain_partial`` does not absorb ``MemoryError`` from drain."""
    queue = _make_queue()
    queue._client = AsyncMock(spec=_ClientStub)
    queue._client.drain.side_effect = MemoryError("oom")

    with pytest.raises(MemoryError):
        await queue._drain_partial()


@pytest.mark.unit
async def test_next_claim_oversize_payload_logs_ack_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversize-payload branch: ``raw.ack()`` failure is logged structured."""
    spy = _patch_logger(monkeypatch)
    queue = _make_queue()
    # ``_MAX_CLAIM_PAYLOAD_BYTES`` is the production cap (1 MiB at the
    # time of writing). Use ``+1`` so the oversize branch reliably
    # fires regardless of the exact limit value.
    huge_payload = b"x" * (_MAX_CLAIM_PAYLOAD_BYTES + 1)
    raw = _FakeMsg(huge_payload)
    raw.ack.side_effect = RuntimeError("ack failed")

    queue._sub = AsyncMock(spec=_SubscriptionStub)
    queue._sub.fetch.return_value = [raw]

    result = await queue.next_claim(timeout=1.0)

    assert result is None
    raw.ack.assert_awaited_once()
    # Two warnings are expected: the payload-too-large preflight and
    # the ack-failure structured log. Pin both shapes.
    warning_kwargs = [c.kwargs for c in spy.warning.call_args_list]
    assert any(kw.get("reason") == "payload_too_large" for kw in warning_kwargs), (
        "expected the oversize-payload preflight warning"
    )
    assert any(
        kw.get("error_type") == "RuntimeError"
        and kw.get("error", "").startswith("RuntimeError:")
        for kw in warning_kwargs
    ), "expected the ack-failure structured warning"


@pytest.mark.unit
async def test_next_claim_malformed_payload_logs_ack_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed-claim branch: ``raw.ack()`` failure is logged structured."""
    spy = _patch_logger(monkeypatch)
    queue = _make_queue()
    raw = _FakeMsg(b"this is not valid json")
    raw.ack.side_effect = RuntimeError("ack failed")

    queue._sub = AsyncMock(spec=_SubscriptionStub)
    queue._sub.fetch.return_value = [raw]

    result = await queue.next_claim(timeout=1.0)

    assert result is None
    raw.ack.assert_awaited_once()
    warning_kwargs = [c.kwargs for c in spy.warning.call_args_list]
    assert any(kw.get("reason") == "validation_failed" for kw in warning_kwargs), (
        "expected the malformed-claim parse-failure warning"
    )
    assert any(
        kw.get("error_type") == "RuntimeError"
        and kw.get("error", "").startswith("RuntimeError:")
        for kw in warning_kwargs
    ), "expected the ack-failure structured warning"


@pytest.mark.unit
async def test_next_claim_oversize_payload_re_raises_memory_error() -> None:
    """``MemoryError`` from ``raw.ack()`` on oversize-payload propagates."""
    queue = _make_queue()
    huge_payload = b"x" * (_MAX_CLAIM_PAYLOAD_BYTES + 1)
    raw = _FakeMsg(huge_payload)
    raw.ack.side_effect = MemoryError("oom")

    queue._sub = AsyncMock(spec=_SubscriptionStub)
    queue._sub.fetch.return_value = [raw]

    with pytest.raises(MemoryError):
        await queue.next_claim(timeout=1.0)
