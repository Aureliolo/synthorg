"""Lifecycle / teardown coverage for ``JetStreamTaskQueue``.

The model-only tests in ``test_claim.py`` do not exercise the
queue client's ``stop()`` / ``_drain_partial()`` / ``next_claim()``
error paths. These tests pin the contract on those paths:

* ``MemoryError`` and ``RecursionError`` propagate unchanged
  (catastrophic interpreter state must reach the supervisor instead
  of being absorbed as a queue-teardown warning).
* Structured ``error_type`` / ``error=safe_error_description(exc)``
  fields land on the warning logs emitted for ordinary failures.

Tests inject mocks directly onto the private ``_sub`` / ``_client``
slots so the suite does not need a live NATS container -- the
public ``start()`` path is unaffected.
"""

from typing import TYPE_CHECKING, Final, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

import synthorg.workers.claim as claim_module
from synthorg.communication.bus.errors import BusStreamError
from synthorg.communication.config import NatsConfig
from synthorg.observability.events.workers import (
    WORKERS_QUEUE_NOT_RUNNING,
    WORKERS_QUEUE_START_REJECTED,
    WORKERS_TASK_QUEUE_PUBLISH_TIMEOUT,
    WORKERS_TASK_QUEUE_UNSUBSCRIBE_FAILED,
)
from synthorg.workers.claim import (
    _MAX_CLAIM_PAYLOAD_BYTES,
    JetStreamTaskQueue,
    TaskClaim,
)
from synthorg.workers.config import QueueConfig

if TYPE_CHECKING:
    from nats.aio.client import Client as NatsClient
    from nats.js import JetStreamContext

    PullSubscription = JetStreamContext.PullSubscription


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
    ) -> list[object]:  # pragma: no cover (spec only)
        return []


class _ClientStub:
    """Concrete spec for the NATS client stand-in (drain only)."""

    async def drain(self) -> None:  # pragma: no cover (spec only)
        ...


class _JetStreamStub:
    """Concrete spec for the JetStream context stand-in (publish only)."""

    async def publish(
        self, subject: str, payload: bytes
    ) -> object:  # pragma: no cover (spec only)
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
    sub = AsyncMock(spec=_SubscriptionStub)
    sub.unsubscribe.side_effect = RuntimeError("transient nats error")
    # Cast to the slot's declared optional type so the post-stop
    # ``is None`` asserts below stay reachable for mypy.
    queue._sub = cast("PullSubscription | None", sub)

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
    client = AsyncMock(spec=_ClientStub)
    client.drain.side_effect = RuntimeError("transient nats error")
    queue._client = cast("NatsClient | None", client)

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
    sub = AsyncMock(spec=_SubscriptionStub)
    sub.unsubscribe.side_effect = RuntimeError("nats")
    queue._sub = cast("PullSubscription | None", sub)
    client = AsyncMock(spec=_ClientStub)
    client.drain.side_effect = RuntimeError("nats drain")
    queue._client = cast("NatsClient | None", client)

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


@pytest.mark.unit
async def test_start_when_running_logs_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second ``start()`` while already running logs the rejection event."""
    spy = _patch_logger(monkeypatch)
    queue = _make_queue()
    queue._running = True

    with pytest.raises(RuntimeError, match="already running"):
        await queue.start()

    assert spy.warning.called
    matched = [
        c
        for c in spy.warning.call_args_list
        if c.args and c.args[0] == WORKERS_QUEUE_START_REJECTED
    ]
    assert len(matched) == 1
    assert matched[0].kwargs["reason"] == "already_running"


@pytest.mark.unit
async def test_publish_claim_before_start_logs_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``publish_claim`` before ``start()`` logs the not-running event."""
    spy = _patch_logger(monkeypatch)
    queue = _make_queue()
    claim = TaskClaim(task_id="task-A", new_status="assigned")

    with pytest.raises(BusStreamError, match="not running"):
        await queue.publish_claim(claim)

    matched = [
        c
        for c in spy.warning.call_args_list
        if c.args and c.args[0] == WORKERS_QUEUE_NOT_RUNNING
    ]
    assert len(matched) == 1
    assert matched[0].kwargs["operation"] == "publish_claim"
    assert matched[0].kwargs["task_id"] == "task-A"


@pytest.mark.unit
async def test_publish_claim_bounded_by_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled ``js.publish`` is deadline-bounded into a ``BusStreamError``.

    ``js.publish`` awaits a server PubAck and is otherwise unbounded; without
    the bound a saturated broker hangs the publish to the caller's wall-clock
    limit (in CI, the suite timeout that SIGABRTs the whole xdist worker). The
    bound turns that stall into a retriable, per-call ``BusStreamError`` and a
    structured warning.
    """
    import asyncio

    spy = _patch_logger(monkeypatch)
    monkeypatch.setattr(claim_module, "_PUBLISH_TIMEOUT_SECONDS", 0.05)
    queue = _make_queue()

    # Far longer than the 0.05s publish deadline above, so the stubbed publish
    # is guaranteed still pending when the bound trips.
    slow_publish_seconds: Final[float] = 10.0

    async def _slow_publish(subject: str, payload: bytes) -> None:
        await asyncio.sleep(slow_publish_seconds)

    js = AsyncMock(spec=_JetStreamStub)
    js.publish.side_effect = _slow_publish
    queue._js = cast("JetStreamContext | None", js)

    claim = TaskClaim(task_id="task-slow", new_status="assigned")
    with pytest.raises(BusStreamError, match="exceeded"):
        await queue.publish_claim(claim)

    matched = [
        c
        for c in spy.warning.call_args_list
        if c.args and c.args[0] == WORKERS_TASK_QUEUE_PUBLISH_TIMEOUT
    ]
    assert len(matched) == 1
    assert matched[0].kwargs["task_id"] == "task-slow"
    assert matched[0].kwargs["error_type"] == "TimeoutError"


@pytest.mark.unit
async def test_next_claim_before_start_logs_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``next_claim`` before ``start()`` logs the not-running event."""
    spy = _patch_logger(monkeypatch)
    queue = _make_queue()

    with pytest.raises(BusStreamError, match="not running"):
        await queue.next_claim(timeout=1.0)

    matched = [
        c
        for c in spy.warning.call_args_list
        if c.args and c.args[0] == WORKERS_QUEUE_NOT_RUNNING
    ]
    assert len(matched) == 1
    assert matched[0].kwargs["operation"] == "next_claim"


# Lifecycle lock + timed-out stop


@pytest.mark.unit
async def test_lifecycle_lock_serialises_concurrent_starts() -> None:
    """Two concurrent ``start()`` calls cannot both pass the ``_running`` check."""
    import asyncio

    queue = _make_queue()
    queue._running = True  # Simulate already-running so any caller hits the guard.

    async def _try_start() -> Exception | None:
        try:
            await queue.start()
        except Exception as exc:
            return exc
        return None

    results = await asyncio.gather(_try_start(), _try_start(), return_exceptions=False)

    # Both calls must observe the running flag and raise the same
    # RuntimeError; the lifecycle lock serialises them so neither
    # silently overwrites the other.
    assert all(isinstance(r, RuntimeError) for r in results)


@pytest.mark.unit
async def test_stop_drain_timeout_marks_unrestartable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stop()`` drain exceeding the deadline raises and flips _stop_failed."""
    import asyncio

    from synthorg.communication.bus.errors import BusStopTimeoutError

    queue = _make_queue()
    queue._stop_drain_timeout_seconds = 0.05

    # Far longer than the 0.05s drain deadline above, so the stubbed drain is
    # guaranteed still running when stop()'s wait_for trips its timeout.
    slow_drain_seconds: Final[float] = 10.0

    async def _slow_drain() -> None:
        await asyncio.sleep(slow_drain_seconds)

    queue._client = AsyncMock(spec=_ClientStub)
    queue._client.drain.side_effect = _slow_drain

    with pytest.raises(BusStopTimeoutError):
        await queue.stop()

    assert queue._stop_failed is True


@pytest.mark.unit
async def test_stop_unsubscribe_timeout_logs_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow ``unsubscribe()`` is deadline-bounded, logged, and stop() proceeds.

    The unsubscribe runs BEFORE the drain in ``stop()``; an unbounded await
    here would stall teardown to the caller's wall-clock limit instead of
    the drain deadline. The bound turns that into a swallowed, structured
    warning so teardown still clears the subscription and reaches the drain.
    """
    import asyncio

    spy = _patch_logger(monkeypatch)
    queue = _make_queue()
    queue._stop_drain_timeout_seconds = 0.05

    # Far longer than the 0.05s unsubscribe deadline, so the stubbed
    # unsubscribe is guaranteed still running when the bound trips.
    slow_unsubscribe_seconds: Final[float] = 10.0

    async def _slow_unsubscribe() -> None:
        await asyncio.sleep(slow_unsubscribe_seconds)

    sub = AsyncMock(spec=_SubscriptionStub)
    sub.unsubscribe.side_effect = _slow_unsubscribe
    queue._sub = cast("PullSubscription | None", sub)

    await queue.stop()

    assert queue._sub is None
    spy.warning.assert_called_once()
    # Assert the telemetry EVENT too, not just the error type: a regression
    # that swapped the event constant would otherwise pass.
    assert spy.warning.call_args.args[0] == WORKERS_TASK_QUEUE_UNSUBSCRIBE_FAILED
    assert spy.warning.call_args.kwargs["error_type"] == "TimeoutError"


@pytest.mark.unit
async def test_start_after_stop_timeout_raises_unrestartable() -> None:
    """``start()`` after a timed-out stop raises ``BusUnrestartableError``."""
    from synthorg.communication.bus.errors import BusUnrestartableError

    queue = _make_queue()
    queue._stop_failed = True

    with pytest.raises(BusUnrestartableError):
        await queue.start()
