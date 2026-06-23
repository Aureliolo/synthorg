"""Tests for EventStreamHub Last-Event-ID replay history (SSE reconnect)."""

import asyncio
from datetime import UTC, datetime

import pytest

from synthorg.communication.event_stream.stream import (
    EventStreamHub,
    EventStreamSubscription,
)
from synthorg.communication.event_stream.types import AgUiEventType, StreamEvent

pytestmark = pytest.mark.unit

_TS = datetime(2026, 4, 13, tzinfo=UTC)
_SESSION = "session-abc"


def _event(event_id: str) -> StreamEvent:
    return StreamEvent(
        id=event_id,
        type=AgUiEventType.RUN_STARTED,
        timestamp=_TS,
        session_id=_SESSION,
    )


def _drain(subscription: EventStreamSubscription) -> list[str]:
    ids: list[str] = []
    while True:
        try:
            ids.append(subscription.get_nowait().id)
        except asyncio.QueueEmpty:
            break
    return ids


async def test_reconnect_replays_only_the_gap() -> None:
    hub = EventStreamHub()
    # Events published while no subscriber is attached (the disconnect gap).
    for i in range(1, 4):
        await hub.publish(_event(f"evt-{i}"))

    # Reconnect carrying the last id the client saw before dropping.
    sub = await hub.subscribe(_SESSION, after_id="evt-1")

    assert _drain(sub) == ["evt-2", "evt-3"]


async def test_reconnect_with_unknown_id_replays_full_buffer() -> None:
    hub = EventStreamHub()
    for i in range(1, 4):
        await hub.publish(_event(f"evt-{i}"))

    sub = await hub.subscribe(_SESSION, after_id="evt-evicted-long-ago")

    assert _drain(sub) == ["evt-1", "evt-2", "evt-3"]


async def test_reconnect_caught_up_replays_nothing() -> None:
    hub = EventStreamHub()
    for i in range(1, 4):
        await hub.publish(_event(f"evt-{i}"))

    sub = await hub.subscribe(_SESSION, after_id="evt-3")

    assert _drain(sub) == []


async def test_no_after_id_replays_nothing() -> None:
    hub = EventStreamHub()
    await hub.publish(_event("evt-1"))

    sub = await hub.subscribe(_SESSION)

    assert _drain(sub) == []


async def test_history_is_bounded_per_session() -> None:
    hub = EventStreamHub(history_per_session=3)
    for i in range(1, 11):
        await hub.publish(_event(f"evt-{i}"))

    # Only the most recent 3 events are retained; replaying from an
    # evicted id yields just the retained tail, never unbounded growth.
    sub = await hub.subscribe(_SESSION, after_id="evt-1")

    assert _drain(sub) == ["evt-8", "evt-9", "evt-10"]
