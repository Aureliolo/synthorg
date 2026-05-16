"""Tests for :class:`EscalationExpirationSweeper`."""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import pytest

from synthorg.communication.conflict_resolution.escalation.in_memory_store import (
    InMemoryEscalationStore,
)
from synthorg.communication.conflict_resolution.escalation.models import (
    Escalation,
    EscalationStatus,
)
from synthorg.communication.conflict_resolution.escalation.sweeper import (
    EscalationExpirationSweeper,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictPosition,
)
from synthorg.communication.enums import ConflictType
from synthorg.core.enums import SeniorityLevel

pytestmark = pytest.mark.unit


def _make_escalation(
    *,
    escalation_id: str,
    expires_at: datetime | None,
) -> Escalation:
    """Build a pending escalation with a configurable deadline."""
    conflict = Conflict(
        id=f"conflict-for-{escalation_id}",
        type=ConflictType.ARCHITECTURE,
        subject="Backend storage engine",
        positions=(
            ConflictPosition(
                agent_id="agent-a",
                agent_department="engineering",
                agent_level=SeniorityLevel.SENIOR,
                position="PostgreSQL",
                reasoning="Strong consistency",
                timestamp=datetime.now(UTC),
            ),
            ConflictPosition(
                agent_id="agent-b",
                agent_department="engineering",
                agent_level=SeniorityLevel.SENIOR,
                position="SQLite",
                reasoning="Simpler ops",
                timestamp=datetime.now(UTC),
            ),
        ),
        detected_at=datetime.now(UTC),
    )
    return Escalation(
        id=escalation_id,
        conflict=conflict,
        created_at=datetime.now(UTC),
        expires_at=expires_at,
    )


class TestSweeperLifecycle:
    async def test_start_is_idempotent(self) -> None:
        store = InMemoryEscalationStore()
        sweeper = EscalationExpirationSweeper(store, interval_seconds=1.0)
        try:
            await sweeper.start()
            first_task = sweeper._task
            await sweeper.start()
            second_task = sweeper._task
            assert first_task is second_task
        finally:
            await sweeper.stop()

    async def test_stop_cancels_task(self) -> None:
        store = InMemoryEscalationStore()
        sweeper = EscalationExpirationSweeper(store, interval_seconds=1.0)
        await sweeper.start()
        task = sweeper._task
        assert task is not None
        await sweeper.stop()
        assert task.done()
        assert sweeper._task is None

    async def test_stop_without_start_is_noop(self) -> None:
        store = InMemoryEscalationStore()
        sweeper = EscalationExpirationSweeper(store, interval_seconds=1.0)
        # Should not raise.
        await sweeper.stop()

    async def test_interval_below_1s_raises(self) -> None:
        store = InMemoryEscalationStore()
        with pytest.raises(ValueError, match="interval_seconds"):
            EscalationExpirationSweeper(store, interval_seconds=0.5)

    async def test_stop_drain_timeout_marks_unrestartable(self) -> None:
        """A stop() drain that exceeds the hard deadline marks the
        sweeper unrestartable so a subsequent start() cannot spawn a
        fresh task while the orphan loop still owns the store.

        Simulates a stuck _run by running a task that suppresses
        CancelledError; the deadline is reduced so the test runs in
        well under a second.
        """
        store = InMemoryEscalationStore()
        sweeper = EscalationExpirationSweeper(store, interval_seconds=1.0)
        await sweeper.start()
        task = sweeper._task
        assert task is not None

        # Replace the real task with one that ignores cancellation by
        # consuming each ``CancelledError`` and re-suspending until a
        # test-only "release" event is set. Production equivalent: a
        # ``_run`` callee that wraps its body in ``except Exception``
        # plus a re-suspending retry loop, swallowing the cancel signal
        # without ever returning. The release event is the explicit
        # teardown hook so the test does not leak the orphan task to
        # xdist after the assertion completes.
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
                    # The cancel signal increments the task's cancel
                    # counter; we explicitly clear it so the next await
                    # does not immediately re-raise. ``uncancel`` was
                    # added in Python 3.11 for exactly this scenario.
                    current = asyncio.current_task()
                    if current is not None:
                        current.uncancel()
                    continue

        task.cancel()
        # Wait the original task out so we do not leak its cancellation.
        with contextlib.suppress(asyncio.CancelledError):
            await task
        stuck_task = asyncio.create_task(_stuck(), name="stuck-sweeper")
        sweeper._task = stuck_task
        sweeper._stop_drain_timeout_seconds = 0.05
        # Yield once so the stuck task starts and reaches its first
        # ``await never`` suspend point. Without this yield the task
        # is still in the "scheduled, not started" state when
        # ``stop()`` calls ``cancel()``, and asyncio satisfies the
        # cancel by simply not starting the coroutine -- the task
        # finishes immediately and the drain returns clean.
        await asyncio.sleep(0)

        with pytest.raises(TimeoutError):
            await sweeper.stop()
        assert sweeper._stop_failed is True

        # A subsequent start() must refuse to spawn a fresh task.
        with pytest.raises(RuntimeError, match="unrestartable"):
            await sweeper.start()

        # Release the stuck task so the event loop can drain on
        # teardown; without this the orphan task survives the test and
        # ``Task was destroyed but it is pending`` warnings poison
        # subsequent xdist workers.
        release_event.set()
        try:
            await asyncio.wait_for(stuck_task, timeout=1.0)
        except asyncio.CancelledError, TimeoutError:
            stuck_task.cancel()


class TestSweeperRunLoop:
    async def test_sweep_expires_stale_rows_on_loop_tick(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One loop tick expires overdue rows without fixed sleeps."""
        store = InMemoryEscalationStore()
        past = datetime.now(UTC) - timedelta(seconds=10)
        stale = _make_escalation(escalation_id="esc-stale", expires_at=past)
        future_deadline = datetime.now(UTC) + timedelta(seconds=3600)
        live = _make_escalation(escalation_id="esc-live", expires_at=future_deadline)
        await store.create(stale)
        await store.create(live)

        first_sweep_done = asyncio.Event()
        original = store.mark_expired

        async def tracked(now_iso: str) -> tuple[str, ...]:
            try:
                return await original(now_iso)
            finally:
                first_sweep_done.set()

        monkeypatch.setattr(store, "mark_expired", tracked)
        sweeper = EscalationExpirationSweeper(store, interval_seconds=1.0)
        await sweeper.start()
        try:
            # Wait for the condition, not a fixed duration, so the test
            # does not flake under scheduler jitter.
            await asyncio.wait_for(first_sweep_done.wait(), timeout=5.0)
        finally:
            await sweeper.stop()

        expired = await store.get("esc-stale")
        alive = await store.get("esc-live")
        assert expired is not None
        assert expired.status == EscalationStatus.EXPIRED
        assert alive is not None
        assert alive.status == EscalationStatus.PENDING

    async def test_sweeper_survives_store_exception(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the store raises, the loop logs + continues to the next tick."""
        store = InMemoryEscalationStore()
        calls = {"n": 0}
        second_tick_done = asyncio.Event()
        original = store.mark_expired

        async def flaky(now_iso: str) -> tuple[str, ...]:
            calls["n"] += 1
            if calls["n"] == 1:
                msg = "simulated transient failure"
                raise RuntimeError(msg)
            result = await original(now_iso)
            second_tick_done.set()
            return result

        monkeypatch.setattr(store, "mark_expired", flaky)
        sweeper = EscalationExpirationSweeper(store, interval_seconds=1.0)
        await sweeper.start()
        try:
            # Condition-based wait: loop survived the first exception
            # and ran at least once more.
            await asyncio.wait_for(second_tick_done.wait(), timeout=5.0)
        finally:
            await sweeper.stop()
        assert calls["n"] >= 2

    async def test_restart_recovery_orphan_pending_gets_expired(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PENDING rows without a live awaiting coroutine get expired.

        Simulates the restart path: a resolver registered a Future, the
        process died (Future gone), the row is still PENDING with a
        past ``expires_at``.  The sweeper must reap it so the queue
        does not leak orphaned entries.
        """
        store = InMemoryEscalationStore()
        past = datetime.now(UTC) - timedelta(seconds=30)
        orphan = _make_escalation(
            escalation_id="esc-orphan",
            expires_at=past,
        )
        await store.create(orphan)
        # No registry, no Future -- only the row in the store.

        first_sweep_done = asyncio.Event()
        original = store.mark_expired

        async def tracked(now_iso: str) -> tuple[str, ...]:
            try:
                return await original(now_iso)
            finally:
                first_sweep_done.set()

        monkeypatch.setattr(store, "mark_expired", tracked)
        sweeper = EscalationExpirationSweeper(store, interval_seconds=1.0)
        await sweeper.start()
        try:
            await asyncio.wait_for(first_sweep_done.wait(), timeout=5.0)
        finally:
            await sweeper.stop()
        row = await store.get("esc-orphan")
        assert row is not None
        assert row.status == EscalationStatus.EXPIRED
