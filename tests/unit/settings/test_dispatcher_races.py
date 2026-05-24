"""TOCTOU race tests for SettingsChangeDispatcher.

Unlike the bridge and engine, settings/dispatcher.py has a real
``await`` between the ``_running`` check and the final ``_running =
True`` assignment: ``_ensure_channel()`` and ``_bus.subscribe()`` both
yield the event loop. Two concurrent ``start()`` calls can both pass
the initial ``if self._running:`` guard, both subscribe to
``#settings``, and both spawn a poll-loop task with duplicate
subscribers attached.

The fix wraps the whole start body in ``_lifecycle_lock`` so exactly
one caller wins.
"""

import asyncio
import contextlib
from typing import override

import pytest

from synthorg.communication.bus.memory import InMemoryMessageBus
from synthorg.communication.config import MessageBusConfig
from synthorg.settings.dispatcher import SettingsChangeDispatcher


@pytest.mark.unit
class TestConcurrentStart:
    """Two concurrent ``start()`` calls must be serialized."""

    async def test_concurrent_start_yields_one_success_one_error(self) -> None:
        """Invariants after ``asyncio.gather(start(), start())``:

        - exactly one start returns successfully
        - exactly one raises ``RuntimeError``
        - at most one poll task named ``settings-dispatcher`` exists
        """
        bus = InMemoryMessageBus(config=MessageBusConfig())
        await bus.start()
        dispatcher = SettingsChangeDispatcher(bus, subscribers=())

        # Coordinated lock forces both ``start()`` callers into the
        # lifecycle-lock critical section concurrently. Without it, the
        # natural asyncio scheduling can let one start() finish before
        # the second contends, so the test would pass on a regression
        # that removed the lock. Only the first two acquires barrier-
        # wait; the ``stop()`` call in ``finally`` bypasses the barrier
        # (which would otherwise deadlock waiting for a second party).
        barrier = asyncio.Barrier(2)
        acquire_count = 0

        class _CoordinatedLock(asyncio.Lock):
            @override
            async def acquire(self) -> bool:  # type: ignore[override]  # asyncio.Lock.acquire returns Literal[True]; we return bool
                nonlocal acquire_count
                acquire_count += 1
                if acquire_count <= 2:
                    await barrier.wait()
                return await super().acquire()

        dispatcher._lifecycle_lock = _CoordinatedLock()

        results = await asyncio.gather(
            dispatcher.start(),
            dispatcher.start(),
            return_exceptions=True,
        )
        try:
            successes = [r for r in results if not isinstance(r, BaseException)]
            failures = [r for r in results if isinstance(r, RuntimeError)]
            assert len(successes) == 1, f"expected one success, got {results!r}"
            assert len(failures) == 1, f"expected one RuntimeError, got {results!r}"
            # Exactly one task should be running for the dispatcher.
            running_tasks = [
                t for t in asyncio.all_tasks() if t.get_name() == "settings-dispatcher"
            ]
            assert len(running_tasks) == 1, (
                f"expected one settings-dispatcher task, got {len(running_tasks)}"
            )
        finally:
            await dispatcher.stop()
            await bus.stop()


@pytest.mark.unit
class TestStartDuringStop:
    """``start()`` racing an in-flight ``stop()`` must wait for stop."""

    async def test_start_waits_for_stop_lock_release(self) -> None:
        """A concurrent start during stop must not spawn a duplicate task.

        With `_lifecycle_lock` guarding both start and stop, the
        second start observes `_running=False` only after stop has
        fully released the lock. Invariant at gather completion:
        exactly one `settings-dispatcher` task exists (the one the
        winning start spawned).

        Deterministic ordering is enforced: we launch ``stop()`` first,
        wait for it to actually acquire ``_lifecycle_lock`` via a
        monkey-patched sentinel on the drain path, then launch
        ``start()``. A plain ``asyncio.gather(stop, start)`` does NOT
        guarantee ``stop()`` grabs the lock first; if ``start()`` runs
        first it raises "already running" and the assertion still
        passes by accident, so the test would not actually pin the
        "start-during-stop" contract it claims to verify.
        """
        bus = InMemoryMessageBus(config=MessageBusConfig())
        await bus.start()
        dispatcher = SettingsChangeDispatcher(bus, subscribers=())
        await dispatcher.start()

        # Sentinel fired from inside stop() *while it holds the real
        # _lifecycle_lock*. We monkey-patch the poll task so that when
        # stop() cancels it and awaits its completion, the task signals
        # the event before returning. That await happens under the
        # lock, so once the event fires the lock is provably held by
        # stop() and a racing start() MUST serialise behind its
        # release.
        stop_holding_lock = asyncio.Event()

        # Replace the in-flight poll task's completion with one that
        # fires the event after observing cancellation. The dispatcher
        # awaits this task inside ``stop()`` while holding
        # ``_lifecycle_lock``, so the event firing is a sound
        # lock-held signal.
        original_task = dispatcher._task
        assert original_task is not None

        blocker = asyncio.Event()

        async def signalling_poll() -> None:
            try:
                # Block cancellably on an Event that is never set;
                # stop() will cancel us. When CancelledError fires,
                # signal the lock-held event before letting
                # cancellation propagate so the test sees the lock
                # being held by the awaiting stop(). Using an Event
                # over ``sleep(large_number)`` per CLAUDE.md guidance.
                await blocker.wait()
            except asyncio.CancelledError:
                stop_holding_lock.set()
                raise

        # Swap the poll task: detach the production
        # ``add_done_callback(self._on_task_done)`` from the original
        # task BEFORE cancelling it, then install ours in its place.
        # Without the detach, the original task's done-callback can
        # race the manual state surgery below and flip ``_running``
        # off (or touch state) after we've swapped in the signalling
        # task -- making the test timing-sensitive. Adding a fresh
        # done-callback to the replacement task keeps the lifecycle
        # hooks consistent with normal operation.
        original_task.remove_done_callback(dispatcher._on_task_done)
        original_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await original_task
        dispatcher._task = asyncio.create_task(
            signalling_poll(),
            name="settings-dispatcher",
        )
        dispatcher._task.add_done_callback(dispatcher._on_task_done)
        # Re-mark the dispatcher as running so stop() proceeds past
        # its early-return guard (the detached-and-cancelled original
        # task cannot flip ``_running`` off since we removed the
        # callback before cancelling).
        dispatcher._running = True

        stop_task = asyncio.create_task(dispatcher.stop())
        await stop_holding_lock.wait()
        start_coro = dispatcher.start()
        results = await asyncio.gather(stop_task, start_coro, return_exceptions=True)
        try:
            assert not isinstance(results[0], BaseException), (
                f"stop raised unexpectedly: {results[0]!r}"
            )
            assert not isinstance(results[1], BaseException), (
                f"start raised unexpectedly: {results[1]!r}"
            )
            running_tasks = [
                t for t in asyncio.all_tasks() if t.get_name() == "settings-dispatcher"
            ]
            assert len(running_tasks) == 1, (
                f"expected one settings-dispatcher after start-during-stop, "
                f"got {len(running_tasks)}"
            )
        finally:
            await dispatcher.stop()
            await bus.stop()
