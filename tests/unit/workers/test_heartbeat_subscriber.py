"""Unit tests for the backend worker-liveness heartbeat subscriber."""

import asyncio
from typing import TYPE_CHECKING, Final, cast

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.workers.config import QueueConfig
from synthorg.workers.heartbeat_models import WorkerHeartbeat
from synthorg.workers.heartbeat_subscriber import WorkerHeartbeatSubscriber
from tests._shared.fake_clock import FakeClock
from tests._shared.fake_task_queue import FakeJetStreamTaskQueue

if TYPE_CHECKING:
    from nats.aio.msg import Msg

pytestmark = pytest.mark.unit

_HARD_CAP_SECONDS: Final[float] = 5.0
_POLL_SECONDS: Final[float] = 0.01


def _beat(worker_id: str, clock: FakeClock, claims_done: int = 0) -> bytes:
    return (
        WorkerHeartbeat(
            worker_id=NotBlankStr(worker_id),
            emitted_at=clock.now(),
            claims_done=claims_done,
        )
        .model_dump_json()
        .encode("utf-8")
    )


def _subscriber(
    queue: FakeJetStreamTaskQueue,
    clock: FakeClock,
) -> WorkerHeartbeatSubscriber:
    return WorkerHeartbeatSubscriber(
        task_queue=queue,  # type: ignore[arg-type]
        queue_config=QueueConfig(enabled=True, heartbeat_interval_seconds=1),
        clock=clock,
    )


async def test_observed_beat_records_last_seen() -> None:
    queue = FakeJetStreamTaskQueue()
    clock = FakeClock()
    sub = _subscriber(queue, clock)

    await sub._on_message(_msg(_beat("w-0", clock, claims_done=4)))

    assert "w-0" in sub._last_seen


async def test_malformed_payload_is_dropped_not_fatal() -> None:
    queue = FakeJetStreamTaskQueue()
    sub = _subscriber(queue, FakeClock())

    await sub._on_message(_msg(b"not-json"))

    assert sub._last_seen == {}


async def test_worker_flagged_stale_then_evicted_and_reregistered() -> None:
    queue = FakeJetStreamTaskQueue()
    clock = FakeClock()
    sub = _subscriber(queue, clock)
    await sub._on_message(_msg(_beat("w-stale", clock)))

    # stale_after = interval(1) * 3 = 3s; advance well past it.
    clock.advance(10.0)
    sub._sweep_once()
    # Flagged stale once, then evicted from both maps so bookkeeping
    # stays bounded under worker-id churn.
    assert "w-stale" not in sub._flagged_stale
    assert "w-stale" not in sub._last_seen

    # A second sweep is a no-op: the worker is gone, so the WARNING
    # cannot fire again (the "once" guarantee, enforced by eviction).
    sub._sweep_once()
    assert "w-stale" not in sub._last_seen

    # A returning beat re-registers the worker as fresh.
    await sub._on_message(_msg(_beat("w-stale", clock)))
    assert "w-stale" in sub._last_seen
    assert "w-stale" not in sub._flagged_stale


async def test_fresh_worker_not_flagged_stale() -> None:
    queue = FakeJetStreamTaskQueue()
    clock = FakeClock()
    sub = _subscriber(queue, clock)
    await sub._on_message(_msg(_beat("w-fresh", clock)))

    clock.advance(1.0)  # within stale_after
    sub._sweep_once()

    assert sub._flagged_stale == set()


async def test_start_stop_lifecycle_and_delivery() -> None:
    queue = FakeJetStreamTaskQueue()
    clock = FakeClock()
    sub = _subscriber(queue, clock)

    await sub.start()
    with pytest.raises(RuntimeError, match="already running"):
        await sub.start()
    assert queue.subscribed_subject == "synthorg.workers.heartbeat.>"

    await queue.deliver_heartbeat(_beat("w-live", clock))
    # Bounded poll: asyncio.timeout is the hard cap; no Event to await.
    async with asyncio.timeout(_HARD_CAP_SECONDS):
        while "w-live" not in sub._last_seen:  # noqa: ASYNC110
            await asyncio.sleep(_POLL_SECONDS)

    await sub.stop()
    await sub.stop()  # idempotent
    assert sub.is_running is False


class _FakeMsg:
    """Raw core-NATS message stub (only ``.data`` is read)."""

    def __init__(self, data: bytes) -> None:
        self.data = data


def _msg(data: bytes) -> Msg:
    """Wrap the stub as ``Msg`` for the typed ``_on_message`` signature."""
    return cast("Msg", _FakeMsg(data))
