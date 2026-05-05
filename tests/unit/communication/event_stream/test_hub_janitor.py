"""Tests for the EventStreamHub inactivity-TTL janitor and lifecycle.

Drives the janitor with ``FakeClock`` so prune timing is deterministic.
The ``advance_async`` yield is critical: without it the janitor task
never observes the post-sleep wakeup before the assertions read
``_subscribers``.
"""

import asyncio
import contextlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from synthorg.communication.event_stream.stream import (
    EventStreamHub,
    EventStreamHubUnrestartableError,
)
from synthorg.communication.event_stream.types import (
    AgUiEventType,
    StreamEvent,
)
from tests._shared.fake_clock import FakeClock


def _make_event(session_id: str) -> StreamEvent:
    return StreamEvent(
        id=f"evt-{uuid4().hex}",
        type=AgUiEventType.RUN_STARTED,
        timestamp=datetime.now(UTC),
        session_id=session_id,
        correlation_id=None,
        agent_id=None,
        payload={},
    )


@pytest.mark.unit
async def test_janitor_prunes_idle_subscribers() -> None:
    """A subscriber that sees no traffic past the TTL is dropped."""
    clock = FakeClock()
    hub = EventStreamHub(clock=clock)
    queue = await hub.subscribe("session-a")
    assert "session-a" in hub._subscribers

    await hub.start(idle_ttl_seconds=10.0, janitor_interval_seconds=1.0)
    try:
        await clock.advance_async(11.0)
        # Drive at least one full janitor tick so the prune runs.
        await clock.advance_async(1.0)
        await clock.advance_async(0.0)
        assert "session-a" not in hub._subscribers
    finally:
        await hub.stop()
    # Drain remains usable post-stop; the hub does not pre-empt drains.
    assert queue.empty()


@pytest.mark.unit
async def test_janitor_keeps_active_subscribers() -> None:
    """A subscriber that keeps receiving events resets its idle clock."""
    clock = FakeClock()
    hub = EventStreamHub(clock=clock, dedup_ttl_seconds=0.0)
    await hub.subscribe("session-active")
    await hub.start(idle_ttl_seconds=10.0, janitor_interval_seconds=1.0)
    try:
        for _ in range(5):
            await hub.publish(_make_event("session-active"))
            await clock.advance_async(2.0)
        assert "session-active" in hub._subscribers
        # After the publishes stop, the subscriber falls past the TTL.
        await clock.advance_async(11.0)
        await clock.advance_async(1.0)
        await clock.advance_async(0.0)
        assert "session-active" not in hub._subscribers
    finally:
        await hub.stop()


@pytest.mark.unit
async def test_lifecycle_start_stop_idempotent() -> None:
    """``start()`` / ``stop()`` tolerate repeated calls."""
    hub = EventStreamHub(clock=FakeClock())
    await hub.start(idle_ttl_seconds=10.0, janitor_interval_seconds=1.0)
    await hub.start(idle_ttl_seconds=10.0, janitor_interval_seconds=1.0)
    await hub.stop()
    await hub.stop()
    assert hub._janitor_task is None


@pytest.mark.unit
async def test_start_rejects_unrestartable_hub() -> None:
    """A hub that timed out during stop refuses a fresh start."""
    hub = EventStreamHub(clock=FakeClock())
    hub._stop_failed = True
    with pytest.raises(EventStreamHubUnrestartableError):
        await hub.start(idle_ttl_seconds=10.0, janitor_interval_seconds=1.0)


@pytest.mark.unit
async def test_publish_bumps_subscriber_last_active() -> None:
    """Publishing to a session refreshes ``last_active`` for its subscribers."""
    clock = FakeClock()
    hub = EventStreamHub(clock=clock, dedup_ttl_seconds=0.0)
    await hub.subscribe("session-x")
    initial = hub._subscribers["session-x"][0].last_active
    await clock.advance_async(5.0)
    await hub.publish(_make_event("session-x"))
    refreshed = hub._subscribers["session-x"][0].last_active
    assert refreshed > initial


@pytest.mark.unit
async def test_start_rejects_non_positive_arguments() -> None:
    """Both lifecycle parameters must be strictly positive."""
    hub = EventStreamHub(clock=FakeClock())
    with pytest.raises(ValueError, match="idle_ttl_seconds"):
        await hub.start(idle_ttl_seconds=0.0, janitor_interval_seconds=1.0)
    with pytest.raises(ValueError, match="janitor_interval_seconds"):
        await hub.start(idle_ttl_seconds=10.0, janitor_interval_seconds=0.0)


@pytest.mark.unit
async def test_subscribe_initialises_last_active() -> None:
    """``subscribe()`` stamps a fresh ``last_active`` from the clock.

    A subscriber whose ``last_active`` is left at the dataclass default
    (0.0) would be eligible for pruning on the very next janitor sweep,
    re-introducing the leak the janitor was added to prevent.
    """
    clock = FakeClock()
    hub = EventStreamHub(clock=clock)
    before = clock.monotonic()
    await hub.subscribe("session-x")
    after = clock.monotonic()
    sub = hub._subscribers["session-x"][0]
    assert before <= sub.last_active <= after


@pytest.mark.unit
async def test_stop_timeout_marks_unrestartable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A drain that hits the deadline forces ``_stop_failed=True``.

    Driving a real "ignores-cancel" coroutine is brittle on 3.14
    because ``CancelledError`` semantics consume the cancellation
    flag on first raise rather than re-raising every await. Stubbing
    ``asyncio.wait_for`` to raise ``TimeoutError`` exercises the
    same branch in ``stop()`` -- timeout caught, ``_stop_failed``
    set, error log fired -- and the follow-up ``start()`` rejection
    is the user-observable contract that matters here.
    """
    hub = EventStreamHub(clock=FakeClock())

    async def _benign_loop() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return

    async with hub._lifecycle_lock_for_current_loop():
        hub._running = True
        hub._janitor_task = asyncio.create_task(
            _benign_loop(),
            name="event-stream-hub-janitor-test",
        )

    async def _raise_timeout(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError

    monkeypatch.setattr(
        "synthorg.communication.event_stream.stream.asyncio.wait_for",
        _raise_timeout,
    )

    await hub.stop(stop_timeout_seconds=0.05)
    assert hub._stop_failed is True
    with pytest.raises(EventStreamHubUnrestartableError):
        await hub.start(idle_ttl_seconds=10.0, janitor_interval_seconds=1.0)
    # Drain the lingering benign loop so xdist's leak detection stays
    # clean. ``_janitor_task`` was cleared by ``stop()`` itself, but the
    # underlying coroutine is still pending; cancel + await drains it.
    name = "event-stream-hub-janitor-test"
    pending = [t for t in asyncio.all_tasks() if t.get_name() == name]
    for t in pending:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"max_queue_size": 0}, "max_queue_size"),
        ({"max_queue_size": -3}, "max_queue_size"),
        ({"dedup_ttl_seconds": -1.0}, "dedup_ttl_seconds"),
        ({"dedup_max_entries_per_session": 0}, "dedup_max_entries_per_session"),
    ],
)
def test_constructor_rejects_invalid_arguments(
    kwargs: dict[str, object],
    match: str,
) -> None:
    """The constructor fail-fasts on out-of-range bounds parameters."""
    with pytest.raises(ValueError, match=match):
        EventStreamHub(**kwargs)  # type: ignore[arg-type]
