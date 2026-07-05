"""Unit tests for the bounded worker sub-task cancel-join helper.

These pin the teardown-safety contract that a plain
``contextlib.suppress(asyncio.CancelledError)`` around ``await task``
violated: the join must be time-bounded (a sub-task wedged on an
unreachable broker cannot hang teardown) AND must not swallow an external
cancellation of the caller (so a shutdown ``asyncio.timeout`` stays
effective instead of being eaten, which is what let a wedged JetStream
ack-extension run a whole test to the module ``SIGABRT``).
"""

import asyncio

import pytest

from synthorg.workers._join import join_cancelled

pytestmark = pytest.mark.unit


async def test_join_returns_when_task_cancels_cleanly() -> None:
    started = asyncio.Event()

    async def clean() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(clean())
    await started.wait()
    task.cancel()
    async with asyncio.timeout(1):
        await join_cancelled(task, "worker-0", "clean")
    assert task.cancelled()


async def test_join_abandons_wedged_task_within_timeout() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def wedged() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Simulate a sub-task wedged in an uncancellable broker await:
            # swallow the cancel and keep blocking until the test releases it.
            await release.wait()

    task = asyncio.create_task(wedged())
    await started.wait()
    task.cancel()
    # The outer timeout would trip if join_cancelled hung on the
    # cancellation-ignoring task; it must instead return at its own deadline.
    async with asyncio.timeout(1):
        await join_cancelled(task, "worker-0", "wedged", timeout_seconds=0.05)
    assert not task.done()  # abandoned as a pending orphan, not awaited forever
    release.set()  # let the orphan finish so the loop tears down clean
    await task


async def test_join_reraises_finished_task_exception() -> None:
    # A sub-task that settles with a real (non-cancellation) exception -- e.g.
    # a critical re-raised through reraise_critical inside the heartbeat /
    # ack-extender loop -- must surface, not be dropped as an asyncio
    # "exception was never retrieved" warning.
    async def boom() -> None:
        msg = "boom"
        raise ValueError(msg)

    task = asyncio.create_task(boom())
    with pytest.raises(ValueError, match="boom"):
        await join_cancelled(task, "worker-0", "boom")


async def test_external_cancel_is_not_swallowed() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def wedged() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    task = asyncio.create_task(wedged())
    await started.wait()
    task.cancel()

    async def caller() -> None:
        # A generous internal deadline: only an EXTERNAL cancel should break
        # this. The old suppress(CancelledError) swallowed it and hung.
        await join_cancelled(task, "worker-0", "wedged", timeout_seconds=1000.0)

    outer = asyncio.create_task(caller())
    await asyncio.sleep(0.05)
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer
    release.set()  # release the orphan for a clean loop teardown
    await task
