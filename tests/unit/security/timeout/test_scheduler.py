"""Unit tests for ApprovalTimeoutScheduler."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus, TimeoutActionType
from synthorg.security.timeout.models import TimeoutAction
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from tests._shared import as_uuid


def _make_pending_item(
    approval_id: str = "approval-test-1",
    action_type: str = "review:task_completion",
) -> ApprovalItem:
    """Build a PENDING ApprovalItem for scheduler tests."""
    return ApprovalItem(
        id=as_uuid(approval_id),
        action_type=action_type,
        title="Test approval",
        description="Test description",
        requested_by="agent-1",
        risk_level=ApprovalRiskLevel.LOW,
        created_at=datetime.now(UTC),
    )


def _make_wait_action() -> TimeoutAction:
    return TimeoutAction(
        action=TimeoutActionType.WAIT,
        reason="Waiting",
    )


def _make_approve_action() -> TimeoutAction:
    return TimeoutAction(
        action=TimeoutActionType.APPROVE,
        reason="Auto-approved by timeout",
    )


def _make_deny_action() -> TimeoutAction:
    return TimeoutAction(
        action=TimeoutActionType.DENY,
        reason="Auto-denied by timeout",
    )


def _make_escalate_action() -> TimeoutAction:
    return TimeoutAction(
        action=TimeoutActionType.ESCALATE,
        reason="Escalated to manager",
        escalate_to="manager",
    )


def _make_mock_store(
    items: tuple[ApprovalItem, ...] = (),
) -> MagicMock:
    """Build a mock ApprovalStore."""
    store = MagicMock()
    store.list_items = AsyncMock(return_value=items)
    store.save_if_pending = AsyncMock(side_effect=lambda item: item)
    return store


def _make_mock_checker(
    action: TimeoutAction | None = None,
) -> MagicMock:
    """Build a mock TimeoutChecker."""
    checker = MagicMock()
    effective_action = action or _make_wait_action()
    # check_and_resolve returns (item, action) -- item may be updated.
    checker.check_and_resolve = AsyncMock(
        side_effect=lambda item: (item, effective_action),
    )
    return checker


@pytest.mark.unit
class TestApprovalTimeoutScheduler:
    """Tests for ApprovalTimeoutScheduler."""

    def test_constructor_rejects_non_positive_interval(self) -> None:
        """interval_seconds must be positive."""
        store = _make_mock_store()
        checker = _make_mock_checker()
        with pytest.raises(ValueError, match="positive"):
            ApprovalTimeoutScheduler(
                approval_store=store,
                timeout_checker=checker,
                interval_seconds=0,
            )

    async def test_start_creates_task(self) -> None:
        """start() creates a background asyncio task."""
        store = _make_mock_store()
        checker = _make_mock_checker()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        await scheduler.start()
        assert scheduler.is_running

        # Cleanup
        await scheduler.stop()

    async def test_start_is_idempotent(self) -> None:
        """Calling start() twice does not create a second task."""
        store = _make_mock_store()
        checker = _make_mock_checker()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        await scheduler.start()
        task1 = scheduler._task
        await scheduler.start()
        task2 = scheduler._task
        assert task1 is task2

        # Cleanup
        await scheduler.stop()

    async def test_stop_cancels_task(self) -> None:
        """stop() cancels the background task and clears it."""
        store = _make_mock_store()
        checker = _make_mock_checker()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        await scheduler.start()
        assert scheduler.is_running
        await scheduler.stop()
        assert not scheduler.is_running

    async def test_stop_when_not_running_is_noop(self) -> None:
        """stop() when not running does nothing."""
        store = _make_mock_store()
        checker = _make_mock_checker()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        # Should not raise
        await scheduler.stop()

    def test_is_running_false_initially(self) -> None:
        """is_running is False before start()."""
        store = _make_mock_store()
        checker = _make_mock_checker()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )
        assert not scheduler.is_running

    def test_reschedule_updates_interval(self) -> None:
        """reschedule() updates the interval."""
        store = _make_mock_store()
        checker = _make_mock_checker()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        scheduler.reschedule(120.0)
        assert scheduler._interval == 120.0

    def test_reschedule_zero_raises(self) -> None:
        """reschedule() with non-positive interval raises ValueError."""
        store = _make_mock_store()
        checker = _make_mock_checker()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        with pytest.raises(ValueError, match="positive"):
            scheduler.reschedule(0)
        with pytest.raises(ValueError, match="positive"):
            scheduler.reschedule(-1)

    async def test_check_pending_evaluates_all_items(self) -> None:
        """_check_pending_approvals evaluates every PENDING item."""
        item1 = _make_pending_item("approval-1")
        item2 = _make_pending_item("approval-2")
        store = _make_mock_store(items=(item1, item2))
        checker = _make_mock_checker(_make_wait_action())
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        await scheduler._check_pending_approvals()

        store.list_items.assert_awaited_once_with(
            status=ApprovalStatus.PENDING,
        )
        assert checker.check_and_resolve.await_count == 2

    async def test_approve_action_saves_and_calls_callback(self) -> None:
        """APPROVE action persists the resolution and calls callback."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        action = _make_approve_action()
        checker = _make_mock_checker(action)
        callback = AsyncMock()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
            on_timeout_resolve=callback,
        )

        await scheduler._check_pending_approvals()

        store.save_if_pending.assert_awaited_once()
        callback.assert_awaited_once()
        saved_item, saved_action = callback.call_args.args
        assert saved_item.id == item.id
        assert saved_action.action == TimeoutActionType.APPROVE

    async def test_deny_action_saves_and_calls_callback(self) -> None:
        """DENY action persists the resolution and calls callback."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        action = _make_deny_action()
        checker = _make_mock_checker(action)
        callback = AsyncMock()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
            on_timeout_resolve=callback,
        )

        await scheduler._check_pending_approvals()

        store.save_if_pending.assert_awaited_once()
        callback.assert_awaited_once()

    async def test_wait_action_skips(self) -> None:
        """WAIT action does not save or call callback."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        checker = _make_mock_checker(_make_wait_action())
        callback = AsyncMock()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
            on_timeout_resolve=callback,
        )

        await scheduler._check_pending_approvals()

        store.save_if_pending.assert_not_awaited()
        callback.assert_not_awaited()

    async def test_escalate_action_logs_only(self) -> None:
        """ESCALATE action logs but does not save or call callback."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        checker = _make_mock_checker(_make_escalate_action())
        callback = AsyncMock()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
            on_timeout_resolve=callback,
        )

        await scheduler._check_pending_approvals()

        store.save_if_pending.assert_not_awaited()
        callback.assert_not_awaited()

    async def test_concurrent_resolution_handled(self) -> None:
        """When save_if_pending returns None (concurrent), callback skipped."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        store.save_if_pending = AsyncMock(return_value=None)
        checker = _make_mock_checker(_make_approve_action())
        callback = AsyncMock()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
            on_timeout_resolve=callback,
        )

        await scheduler._check_pending_approvals()

        store.save_if_pending.assert_awaited_once()
        callback.assert_not_awaited()

    async def test_poll_error_swallowed(self) -> None:
        """Errors from list_items are logged and swallowed."""
        store = _make_mock_store()
        store.list_items = AsyncMock(
            side_effect=RuntimeError("connection lost"),
        )
        checker = _make_mock_checker()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        # Should not raise
        await scheduler._check_pending_approvals()

        checker.check_and_resolve.assert_not_awaited()

    async def test_callback_error_swallowed(self) -> None:
        """Errors from on_timeout_resolve are logged and swallowed."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        checker = _make_mock_checker(_make_approve_action())
        callback = AsyncMock(side_effect=RuntimeError("callback failed"))
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
            on_timeout_resolve=callback,
        )

        # Should not raise
        await scheduler._check_pending_approvals()

        callback.assert_awaited_once()

    async def test_no_callback_still_works(self) -> None:
        """Without on_timeout_resolve, resolution still persists."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        checker = _make_mock_checker(_make_approve_action())
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        await scheduler._check_pending_approvals()

        store.save_if_pending.assert_awaited_once()

    async def test_save_error_swallowed(self) -> None:
        """Errors from save_if_pending are logged and swallowed."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        store.save_if_pending = AsyncMock(
            side_effect=RuntimeError("db down"),
        )
        checker = _make_mock_checker(_make_approve_action())
        callback = AsyncMock()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
            on_timeout_resolve=callback,
        )

        # Should not raise
        await scheduler._check_pending_approvals()

        callback.assert_not_awaited()

    async def test_check_error_swallowed(self) -> None:
        """Errors from check_and_resolve are logged and swallowed."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        checker = _make_mock_checker()
        checker.check_and_resolve = AsyncMock(
            side_effect=RuntimeError("policy error"),
        )
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        # Should not raise
        await scheduler._check_pending_approvals()

    async def test_check_pending_called_directly(self) -> None:
        """Verify _check_pending_approvals can be invoked directly."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        checker = _make_mock_checker(_make_wait_action())
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        # Call directly instead of relying on real-time loop ticking.
        await scheduler._check_pending_approvals()

        store.list_items.assert_awaited_once()
        checker.check_and_resolve.assert_awaited_once()

    @pytest.mark.parametrize(
        "error_cls",
        [MemoryError, RecursionError],
        ids=["MemoryError", "RecursionError"],
    )
    async def test_poll_memory_error_propagates(
        self, error_cls: type[BaseException]
    ) -> None:
        """MemoryError/RecursionError from list_items propagates."""
        store = _make_mock_store()
        store.list_items = AsyncMock(side_effect=error_cls("fatal"))
        checker = _make_mock_checker()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        with pytest.raises(error_cls):
            await scheduler._check_pending_approvals()

    @pytest.mark.parametrize(
        "error_cls",
        [MemoryError, RecursionError],
        ids=["MemoryError", "RecursionError"],
    )
    async def test_check_memory_error_propagates(
        self, error_cls: type[BaseException]
    ) -> None:
        """MemoryError/RecursionError from check_and_resolve propagates."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        checker = _make_mock_checker()
        checker.check_and_resolve = AsyncMock(side_effect=error_cls("fatal"))
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        with pytest.raises(error_cls):
            await scheduler._check_pending_approvals()

    @pytest.mark.parametrize(
        "error_cls",
        [MemoryError, RecursionError],
        ids=["MemoryError", "RecursionError"],
    )
    async def test_save_memory_error_propagates(
        self, error_cls: type[BaseException]
    ) -> None:
        """MemoryError/RecursionError from save_if_pending propagates."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        store.save_if_pending = AsyncMock(side_effect=error_cls("fatal"))
        checker = _make_mock_checker(_make_approve_action())
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        with pytest.raises(error_cls):
            await scheduler._check_pending_approvals()

    @pytest.mark.parametrize(
        "error_cls",
        [MemoryError, RecursionError],
        ids=["MemoryError", "RecursionError"],
    )
    async def test_callback_memory_error_propagates(
        self, error_cls: type[BaseException]
    ) -> None:
        """MemoryError/RecursionError from on_timeout_resolve propagates."""
        item = _make_pending_item()
        store = _make_mock_store(items=(item,))
        checker = _make_mock_checker(_make_approve_action())
        callback = AsyncMock(side_effect=error_cls("fatal"))
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
            on_timeout_resolve=callback,
        )

        with pytest.raises(error_cls):
            await scheduler._check_pending_approvals()

    async def test_reschedule_sets_wake_event_when_running(self) -> None:
        """reschedule() sets the wake event to interrupt the sleep loop.

        Verified after ``start()`` because the loop-bound wake event is
        deferred until then; calling reschedule on an unstarted scheduler
        is still legal but only updates the interval (no event to wake).

        ``_run_loop`` is stubbed so the spawned task exits immediately
        and never consumes the wake event; the assertion below is
        otherwise vulnerable to a race where the live scheduler task
        clears the event between ``reschedule()`` setting it and the
        ``is_set()`` check.
        """
        store = _make_mock_store()
        checker = _make_mock_checker()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        async def _noop_loop(_self: ApprovalTimeoutScheduler) -> None:
            return

        with patch.object(ApprovalTimeoutScheduler, "_run_loop", _noop_loop):
            await scheduler.start()
            try:
                assert scheduler._wake_event is not None
                scheduler._wake_event.clear()
                scheduler.reschedule(120.0)
                assert scheduler._wake_event.is_set()
                assert scheduler._interval == 120.0
            finally:
                await scheduler.stop()

    def test_reschedule_before_start_updates_interval(self) -> None:
        """reschedule() before start() updates the interval but skips waking.

        Loop-bound primitives are not yet created at this point; the
        next ``start()`` will see the updated interval.
        """
        store = _make_mock_store()
        checker = _make_mock_checker()
        scheduler = ApprovalTimeoutScheduler(
            approval_store=store,
            timeout_checker=checker,
            interval_seconds=60.0,
        )

        scheduler.reschedule(120.0)
        assert scheduler._interval == 120.0
        assert scheduler._wake_event is None
