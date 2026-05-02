"""Canonical lifecycle pattern tests for ``OrgInflectionMonitor``.

The unit ``test_monitor.py`` covers happy-path start / stop / tick.
This module verifies the lock-driven concurrency safety added per
``docs/reference/lifecycle-sync.md``: concurrent start, restart after
clean stop, and unrestartable flag after a drain timeout.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.meta.chief_of_staff.inflection import OrgInflectionDetector
from synthorg.meta.chief_of_staff.monitor import (
    InflectionMonitorLifecycleError,
    OrgInflectionMonitor,
)
from synthorg.meta.signals.snapshot import SnapshotBuilder

pytestmark = pytest.mark.unit


def _make_monitor() -> OrgInflectionMonitor:
    # ``spec=SnapshotBuilder`` auto-mocks ``build`` as an AsyncMock;
    # set ``return_value`` instead of reassigning ``builder.build``.
    builder = AsyncMock(spec=SnapshotBuilder)
    builder.build.return_value = None
    return OrgInflectionMonitor(
        detector=OrgInflectionDetector(),
        snapshot_builder=builder,
        sinks=(),
        check_interval_minutes=60,
    )


class TestOrgInflectionMonitorLifecycleLock:
    """Canonical pattern compliance."""

    async def test_concurrent_starts_spawn_one_task(self) -> None:
        # The first spawned monitor task must still be alive when
        # the peer ``start()`` calls run -- otherwise a fast-finishing
        # builder lets the first task complete before the others
        # land, the lifecycle lock releases, and the test cannot
        # distinguish "lock works" from "lock leaked but task already
        # finished". Block ``builder.build`` on a controllable Event
        # so the spawned task is guaranteed to be running through
        # the gather.
        block = asyncio.Event()

        async def blocking_build(*_args: object, **_kwargs: object) -> None:
            await block.wait()

        builder = AsyncMock(spec=SnapshotBuilder)
        builder.build.side_effect = blocking_build
        monitor = OrgInflectionMonitor(
            detector=OrgInflectionDetector(),
            snapshot_builder=builder,
            sinks=(),
            check_interval_minutes=60,
        )

        original_create_task = asyncio.create_task
        spawned: list[asyncio.Task[object]] = []

        def _counting_create_task(
            coro: object,
            **kwargs: object,
        ) -> asyncio.Task[object]:
            task: asyncio.Task[object] = original_create_task(coro, **kwargs)  # type: ignore[arg-type]
            spawned.append(task)
            return task

        try:
            with patch(
                "synthorg.meta.chief_of_staff.monitor.asyncio.create_task",
                _counting_create_task,
            ):
                await asyncio.gather(
                    monitor.start(),
                    monitor.start(),
                    monitor.start(),
                )
            assert monitor._task is not None
            assert len(spawned) == 1
        finally:
            block.set()
            await monitor.stop()

    async def test_restart_after_clean_stop(self) -> None:
        monitor = _make_monitor()
        await monitor.start()
        await monitor.stop()
        # Cannot assert ``_task is None`` between the calls -- mypy
        # narrows the type and flags the subsequent ``start()`` as
        # unreachable.
        await monitor.start()
        # Positive assertion: the second ``start()`` actually
        # rebuilds the loop task. Without this, a regression where
        # ``start()`` silently no-ops after a stop would still pass
        # the test.
        assert monitor._task is not None
        await monitor.stop()

    async def test_unrestartable_after_drain_timeout(self) -> None:
        monitor = _make_monitor()
        monitor._stop_drain_timeout_seconds = 0.05
        # ``release`` lets the test wake the patched loop after the
        # timeout assertion, so the test never leaves a wall-clock-
        # sensitive ``asyncio.sleep(1.0)`` task hanging in the
        # background. Without this, the next test can race the
        # leftover task and observe `_task` from this one.
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hung_loop(self: OrgInflectionMonitor) -> None:
            del self
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Suppress cancellation -- simulates a stuck drain --
                # but block on a controllable event instead of sleep
                # so the test can release the loop deterministically.
                await release.wait()

        with patch.object(OrgInflectionMonitor, "_loop", hung_loop):
            await monitor.start()
            await entered.wait()
            saved_task = monitor._task
            try:
                with pytest.raises(TimeoutError):
                    await monitor.stop()
                assert monitor._stop_failed is True
                assert saved_task is not None
            finally:
                # ``finally`` so a failed assertion above (e.g. the
                # service forgot to mark itself unrestartable) still
                # releases the hung loop and drains the orphan task.
                # Otherwise the leak would silently propagate into
                # the next test run.
                #
                # ``suppress(CancelledError)`` covers the canonical
                # post-cancel state: ``stop()`` called ``task.cancel()``
                # and the hung loop caught it, but the task remains
                # marked cancelled in Python 3.11+ semantics. Awaiting
                # it after ``release.set()`` re-raises the residual
                # cancellation; that's expected, not a regression.
                release.set()
                if saved_task is not None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await saved_task

        with pytest.raises(InflectionMonitorLifecycleError, match="unrestartable"):
            await monitor.start()
