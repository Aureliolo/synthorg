"""Lifecycle-lock and unrestartable-flag tests for ApprovalTimeoutScheduler.

Covers the canonical lifecycle pattern from
``docs/reference/lifecycle-sync.md``:

* ``start()`` is idempotent under concurrent callers (the lifecycle
  lock prevents duplicate task spawning).
* ``stop(timeout=...)`` sets the unrestartable ``_stop_failed`` flag
  on drain timeout so a subsequent ``start()`` raises ``RuntimeError``.
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler

pytestmark = pytest.mark.unit


def _make_store() -> MagicMock:
    from synthorg.approval.protocol import ApprovalStoreProtocol

    store = MagicMock(spec=ApprovalStoreProtocol)
    store.list_items = AsyncMock(
        spec=ApprovalStoreProtocol.list_items,
        return_value=(),
    )
    store.save_if_pending = AsyncMock(
        spec=ApprovalStoreProtocol.save_if_pending,
        side_effect=lambda item: ApprovalItem(
            id=item.id,
            action_type=item.action_type,
            title=item.title,
            description=item.description,
            requested_by=item.requested_by,
            risk_level=ApprovalRiskLevel.LOW,
            created_at=datetime.now(UTC),
        ),
    )
    return store


def _make_checker() -> MagicMock:
    from synthorg.security.timeout.timeout_checker import TimeoutChecker

    checker = MagicMock(spec=TimeoutChecker)
    checker.check_and_resolve = AsyncMock(
        spec=TimeoutChecker.check_and_resolve,
        side_effect=lambda item: (item, None),
    )
    return checker


class TestSchedulerLifecycleLock:
    async def test_concurrent_start_calls_spawn_only_one_task(self) -> None:
        """asyncio.gather of two start() calls produces a single task.

        Without the lifecycle lock, both callers could pass the
        ``is_running`` guard before either reaches the
        ``asyncio.create_task`` line, spawning duplicate scheduler
        loops on the same store.
        """
        scheduler = ApprovalTimeoutScheduler(
            approval_store=_make_store(),
            timeout_checker=_make_checker(),
            interval_seconds=60.0,
        )
        try:
            # Patch the underlying task spawn so the test asserts on
            # call count, not just on the post-condition snapshot.
            # ``is_running`` and a non-None ``_task`` could both be
            # true even if a brief duplicate task got created and
            # immediately collapsed; counting spawns is the only way
            # to prove the lifecycle lock actually serialised.
            with patch(
                "asyncio.create_task",
                wraps=asyncio.create_task,
            ) as create_task:
                await asyncio.gather(scheduler.start(), scheduler.start())
            assert create_task.call_count == 1
            assert scheduler.is_running
            first_task = scheduler._task
            assert first_task is not None
        finally:
            await scheduler.stop()


class TestSchedulerStopFailedFlag:
    async def test_stop_timeout_marks_unrestartable(self) -> None:
        """A drain that exceeds ``timeout`` sets ``_stop_failed`` and raises.

        Construction of a fresh scheduler is the documented recovery
        path because the prior task may still be in flight finishing
        its cleanup; a new task spawned alongside it would break the
        single-writer invariant.
        """
        scheduler = ApprovalTimeoutScheduler(
            approval_store=_make_store(),
            timeout_checker=_make_checker(),
            interval_seconds=60.0,
        )
        await scheduler.start()

        # Patch the inner cancel-and-drain to hang so the wait_for
        # bound trips. ``asyncio.Event().wait()`` blocks indefinitely
        # but is cancellation-safe, so the wait_for can still fire its
        # TimeoutError without leaking the helper coroutine.
        async def _hang() -> None:
            await asyncio.Event().wait()

        scheduler._cancel_and_drain = _hang  # type: ignore[method-assign]
        with pytest.raises(TimeoutError):
            await scheduler.stop(timeout=0.05)
        assert scheduler._stop_failed is True

    async def test_start_after_stop_failed_raises_runtime_error(self) -> None:
        """Once ``_stop_failed`` is set, ``start()`` refuses to spawn."""
        scheduler = ApprovalTimeoutScheduler(
            approval_store=_make_store(),
            timeout_checker=_make_checker(),
            interval_seconds=60.0,
        )
        scheduler._stop_failed = True
        with pytest.raises(RuntimeError, match="unrestartable"):
            await scheduler.start()

    async def test_stop_negative_timeout_raises_value_error(self) -> None:
        """Bounds-check the timeout argument at the system boundary."""
        scheduler = ApprovalTimeoutScheduler(
            approval_store=_make_store(),
            timeout_checker=_make_checker(),
            interval_seconds=60.0,
        )
        with pytest.raises(ValueError, match="must be > 0"):
            await scheduler.stop(timeout=0)
