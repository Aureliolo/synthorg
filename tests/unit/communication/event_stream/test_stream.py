"""Tests for EventStreamHub."""

import asyncio
from datetime import UTC, datetime

import pytest

from synthorg.communication.event_stream.stream import (
    EventStreamHub,
    EventStreamSubscription,
)
from synthorg.communication.event_stream.types import AgUiEventType, StreamEvent

_TS = datetime(2026, 4, 13, tzinfo=UTC)


def _make_event(
    session_id: str = "session-abc",
    event_type: AgUiEventType = AgUiEventType.RUN_STARTED,
) -> StreamEvent:
    return StreamEvent(
        id="evt-001",
        type=event_type,
        timestamp=_TS,
        session_id=session_id,
    )


@pytest.mark.unit
class TestEventStreamHub:
    async def test_subscribe_returns_subscription(self) -> None:
        hub = EventStreamHub()
        queue = await hub.subscribe("session-abc")
        assert isinstance(queue, EventStreamSubscription)

    async def test_publish_delivers_to_subscriber(self) -> None:
        hub = EventStreamHub()
        queue = await hub.subscribe("session-abc")
        event = _make_event()
        await hub.publish(event)
        received = queue.get_nowait()
        assert received.id == "evt-001"

    async def test_publish_fans_out_to_multiple_subscribers(self) -> None:
        hub = EventStreamHub()
        q1 = await hub.subscribe("session-abc")
        q2 = await hub.subscribe("session-abc")
        event = _make_event()
        await hub.publish(event)
        assert q1.get_nowait().id == "evt-001"
        assert q2.get_nowait().id == "evt-001"

    async def test_publish_only_to_matching_session(self) -> None:
        hub = EventStreamHub()
        q_abc = await hub.subscribe("session-abc")
        q_xyz = await hub.subscribe("session-xyz")
        event = _make_event(session_id="session-abc")
        await hub.publish(event)
        assert q_abc.get_nowait().id == "evt-001"
        assert q_xyz.empty()

    async def test_unsubscribe_removes_queue(self) -> None:
        hub = EventStreamHub()
        queue = await hub.subscribe("session-abc")
        await hub.unsubscribe(queue)
        event = _make_event()
        await hub.publish(event)
        assert queue.empty()

    async def test_unsubscribe_unknown_session_no_error(self) -> None:
        hub = EventStreamHub()
        subscription = EventStreamSubscription("nonexistent", asyncio.Queue())
        await hub.unsubscribe(subscription)

    async def test_publish_to_session_with_no_subscribers(self) -> None:
        hub = EventStreamHub()
        event = _make_event(session_id="orphan")
        await hub.publish(event)  # should not raise

    async def test_full_queue_does_not_block(self) -> None:
        hub = EventStreamHub(max_queue_size=1)
        queue = await hub.subscribe("session-abc")
        e1 = _make_event()
        e2 = StreamEvent(
            id="evt-002",
            type=AgUiEventType.RUN_FINISHED,
            timestamp=_TS,
            session_id="session-abc",
        )
        await hub.publish(e1)
        await hub.publish(e2)  # queue full, should not block
        assert queue.qsize() == 1
        kept = queue.get_nowait()
        assert kept.id == "evt-001"  # first event kept, second dropped
        assert queue.empty()

    async def test_publish_raw_convenience(self) -> None:
        hub = EventStreamHub()
        queue = await hub.subscribe("session-abc")
        await hub.publish_raw(
            session_id="session-abc",
            event_type=AgUiEventType.STEP_STARTED,
            agent_id="agent-001",
            payload={"step": 1},
        )
        event = queue.get_nowait()
        assert event.type == AgUiEventType.STEP_STARTED
        assert event.agent_id == "agent-001"
        assert event.payload["step"] == 1

    async def test_multiple_sessions_isolated(self) -> None:
        hub = EventStreamHub()
        q1 = await hub.subscribe("s1")
        q2 = await hub.subscribe("s2")
        await hub.publish(_make_event(session_id="s1"))
        await hub.publish(
            StreamEvent(
                id="evt-s2",
                type=AgUiEventType.RUN_FINISHED,
                timestamp=_TS,
                session_id="s2",
            ),
        )
        assert q1.get_nowait().session_id == "s1"
        assert q2.get_nowait().session_id == "s2"
        assert q1.empty()
        assert q2.empty()


@pytest.mark.unit
class TestEventStreamHubRaceConditions:
    """Race-condition regression tests.

    These tests synchronize many concurrent tasks at the start of the
    critical section via :class:`asyncio.Barrier` so they all attempt
    the unsynchronized dict mutation simultaneously.  Without the
    ``_lock``, ``setdefault().append()`` could lose subscribers or
    drop events; the assertions below would intermittently fail.
    """

    async def test_concurrent_subscribers_for_same_session_all_receive_event(
        self,
    ) -> None:
        hub = EventStreamHub()
        n_subscribers = 100
        barrier = asyncio.Barrier(n_subscribers)

        async def subscribe_under_barrier() -> EventStreamSubscription:
            await barrier.wait()
            return await hub.subscribe("race-session")

        queues = await asyncio.gather(
            *(subscribe_under_barrier() for _ in range(n_subscribers)),
        )
        assert len({id(q) for q in queues}) == n_subscribers

        await hub.publish(_make_event(session_id="race-session"))

        for queue in queues:
            received = queue.get_nowait()
            assert received.id == "evt-001"

    async def test_concurrent_subscribe_unsubscribe_no_corruption(self) -> None:
        hub = EventStreamHub()
        n_subscribers = 50
        barrier = asyncio.Barrier(n_subscribers * 2)

        async def subscribe_then_unsubscribe() -> None:
            await barrier.wait()
            queue = await hub.subscribe("race-session")
            await hub.unsubscribe(queue)

        async def publish_repeatedly() -> None:
            await barrier.wait()
            for _ in range(10):
                await hub.publish(_make_event(session_id="race-session"))

        # Half subscribe/unsubscribe, half publish; barrier holds them all
        # until every coroutine is at the start.  No KeyError or
        # corruption should occur.
        await asyncio.gather(
            *(subscribe_then_unsubscribe() for _ in range(n_subscribers)),
            *(publish_repeatedly() for _ in range(n_subscribers)),
        )
