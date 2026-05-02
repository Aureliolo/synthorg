"""Dedup tests for ``EventStreamHub.publish``.

Per audit #133: a retried publish (e.g. a webhook handler that
catches a transient publish failure) must not deliver the same event
twice to subscribers. The hub keeps a per-session sliding-window of
seen ``event.id`` values; identical ids within the TTL are skipped
and logged.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.event_stream.types import (
    AgUiEventType,
    StreamEvent,
)
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


def _event(*, event_id: str, session_id: str = "session-1") -> StreamEvent:
    return StreamEvent(
        id=event_id,
        type=AgUiEventType.TEXT_MESSAGE_CONTENT,
        timestamp=datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC),
        session_id=session_id,
        correlation_id=None,
        agent_id=None,
        payload={},
    )


class TestEventStreamHubDedup:
    """Per-session dedup window."""

    async def test_duplicate_event_id_within_ttl_is_skipped(self) -> None:
        clock = FakeClock()
        hub = EventStreamHub(dedup_ttl_seconds=60.0, clock=clock)
        queue = await hub.subscribe("session-1")

        event = _event(event_id="evt-001")
        await hub.publish(event)
        await hub.publish(event)  # duplicate

        delivered: list[StreamEvent] = []
        try:
            while True:
                delivered.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            pass
        assert len(delivered) == 1

    async def test_duplicate_after_ttl_is_redelivered(self) -> None:
        clock = FakeClock()
        hub = EventStreamHub(dedup_ttl_seconds=60.0, clock=clock)
        queue = await hub.subscribe("session-1")

        event = _event(event_id="evt-001")
        await hub.publish(event)
        clock.advance(61.0)
        await hub.publish(event)

        delivered: list[StreamEvent] = []
        try:
            while True:
                delivered.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            pass
        assert len(delivered) == 2

    async def test_distinct_ids_all_delivered(self) -> None:
        clock = FakeClock()
        hub = EventStreamHub(dedup_ttl_seconds=60.0, clock=clock)
        queue = await hub.subscribe("session-1")

        for i in range(5):
            await hub.publish(_event(event_id=f"evt-{i}"))

        delivered: list[StreamEvent] = []
        try:
            while True:
                delivered.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            pass
        assert len(delivered) == 5

    async def test_different_sessions_independent_dedup(self) -> None:
        clock = FakeClock()
        hub = EventStreamHub(dedup_ttl_seconds=60.0, clock=clock)
        q1 = await hub.subscribe("session-1")
        q2 = await hub.subscribe("session-2")

        # Same event id, different sessions -- must each receive one.
        await hub.publish(_event(event_id="shared-id", session_id="session-1"))
        await hub.publish(_event(event_id="shared-id", session_id="session-2"))

        assert q1.qsize() == 1
        assert q2.qsize() == 1

    async def test_dedup_window_bounded_per_session(self) -> None:
        """The per-session map is bounded so a noisy session cannot leak memory."""
        clock = FakeClock()
        hub = EventStreamHub(
            dedup_ttl_seconds=300.0,
            dedup_max_entries_per_session=8,
            clock=clock,
        )
        await hub.subscribe("session-1")
        for i in range(100):
            await hub.publish(_event(event_id=f"evt-{i}"))
        seen = hub._seen_event_ids["session-1"]
        assert len(seen) <= 8
