"""Tests for :mod:`synthorg.communication.conflict_resolution.escalation.notify`.

Focuses on lifecycle branches added in the #1683 reliability bundle:

* The ``_stop_failed`` unrestartable guard refuses a fresh ``start()``
  after a ``stop()`` drain that exceeded the hard deadline.
* The hard-deadline drain raises ``TimeoutError`` instead of blocking
  ``stop()`` indefinitely on a callee that suppresses
  ``CancelledError``.

The Postgres LISTEN/NOTIFY plumbing itself is exercised in the
persistence-layer integration suite; here we patch the run loop to
isolate the lifecycle behaviour.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest

from synthorg.communication.conflict_resolution.escalation.notify import (
    NoopEscalationNotifySubscriber,
    PostgresEscalationNotifySubscriber,
)
from synthorg.communication.conflict_resolution.escalation.protocol import (
    EscalationQueueStore,
)
from synthorg.communication.conflict_resolution.escalation.registry import (
    PendingFuturesRegistry,
)

pytestmark = pytest.mark.unit


class TestNoopSubscriber:
    async def test_start_stop_are_noops(self) -> None:
        """The Noop variant exists for SQLite/in-memory deployments."""
        subscriber = NoopEscalationNotifySubscriber()
        await subscriber.start()
        await subscriber.stop()


class TestPostgresSubscriberValidation:
    async def test_invalid_channel_rejected(self) -> None:
        """Defence-in-depth: hand-constructed unsafe channel raises."""
        repo = AsyncMock(spec=EscalationQueueStore)
        registry = AsyncMock(spec=PendingFuturesRegistry)
        # The constructor must REJECT this literal, so it has to appear
        # verbatim on the channel= argument; the trailing marker keeps
        # the persistence-boundary gate quiet.
        bad_channel = "bad; DROP TABLE"  # lint-allow: persistence-boundary -- negative-path literal asserted REJECTED  # noqa: E501
        with pytest.raises(ValueError, match="not a safe Postgres identifier"):
            PostgresEscalationNotifySubscriber(
                repo,
                registry,
                channel=bad_channel,
            )

    async def test_negative_reconnect_delay_rejected(self) -> None:
        repo = AsyncMock(spec=EscalationQueueStore)
        registry = AsyncMock(spec=PendingFuturesRegistry)
        with pytest.raises(ValueError, match="reconnect_delay_seconds"):
            PostgresEscalationNotifySubscriber(
                repo,
                registry,
                channel="escalations",
                reconnect_delay_seconds=0,
            )


class TestPostgresSubscriberLifecycle:
    async def test_stop_drain_timeout_marks_unrestartable(self) -> None:
        """A ``stop()`` drain that exceeds the hard deadline marks the
        subscriber unrestartable so a subsequent ``start()`` cannot
        spawn a fresh task while the orphan loop still holds the
        LISTEN connection.
        """
        repo = AsyncMock(spec=EscalationQueueStore)
        registry = AsyncMock(spec=PendingFuturesRegistry)
        subscriber = PostgresEscalationNotifySubscriber(
            repo,
            registry,
            channel="escalations",
            reconnect_delay_seconds=1.0,
        )
        await subscriber.start()
        original_task = subscriber._task
        assert original_task is not None
        # Cancel + await the real ``_run`` so we do not leak it.
        original_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await original_task

        # Replace ``_task`` with a coroutine that swallows cancellation
        # using the ``uncancel`` recipe (Python 3.11+). The release
        # event lets the test reliably reap the orphan during
        # teardown so xdist does not see a pending task at exit.
        release_event = asyncio.Event()

        async def _stuck() -> None:
            never = asyncio.get_event_loop().create_future()
            while not release_event.is_set():
                try:
                    await asyncio.wait(
                        [never, asyncio.create_task(release_event.wait())],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    current = asyncio.current_task()
                    if current is not None:
                        current.uncancel()
                    continue

        stuck_task = asyncio.create_task(_stuck(), name="stuck-notify")
        subscriber._task = stuck_task
        subscriber._stop_drain_timeout_seconds = 0.05
        # Yield once so ``_stuck`` reaches its first suspend point;
        # without this asyncio satisfies the cancel by simply not
        # starting the coroutine.
        await asyncio.sleep(0)

        with pytest.raises(TimeoutError):
            await subscriber.stop()
        assert subscriber._stop_failed is True

        # A subsequent start() must refuse to spawn a fresh task.
        with pytest.raises(RuntimeError, match="unrestartable"):
            await subscriber.start()

        # Tear down the orphan stuck task so xdist does not inherit
        # the leaked future.
        release_event.set()
        try:
            await asyncio.wait_for(stuck_task, timeout=1.0)
        except asyncio.CancelledError, TimeoutError:
            stuck_task.cancel()
