"""Regression tests: scheduler tracks fire-and-forget escalation notifications.

Issue #1404 -- the approval-escalation notification must be tracked
via :class:`BackgroundTaskRegistry` so failures surface at ERROR with
``NOTIFICATION_SEND_FAILED``.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, TimeoutActionType
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability.events.notification import (
    NOTIFICATION_ESCALATION_SEND,
    NOTIFICATION_SEND_FAILED,
)
from synthorg.security.timeout.models import TimeoutAction
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.security.timeout.timeout_checker import TimeoutChecker

pytestmark = pytest.mark.unit


_NOTIFY_ERROR_MSG = "notification service down"


async def _raising_notify(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError(_NOTIFY_ERROR_MSG)


async def test_escalation_notification_failure_logs_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing escalation notification logs NOTIFICATION_SEND_FAILED."""
    item = ApprovalItem(
        id="approval-x",
        action_type="review:task_completion",
        title="Test",
        description="Test",
        requested_by="agent-1",
        risk_level=ApprovalRiskLevel.HIGH,
        created_at=datetime.now(UTC),
    )
    action = TimeoutAction(
        action=TimeoutActionType.ESCALATE,
        reason="manager review needed",
        escalate_to="manager",
    )

    checker = MagicMock(spec=TimeoutChecker)
    checker.check_and_resolve = AsyncMock(
        spec=TimeoutChecker.check_and_resolve,
        return_value=(item, action),
    )
    dispatcher = AsyncMock(spec=NotificationDispatcher)

    scheduler = ApprovalTimeoutScheduler(
        approval_store=MagicMock(spec=ApprovalStoreProtocol),
        timeout_checker=checker,
        interval_seconds=60.0,
        notification_dispatcher=dispatcher,
    )
    monkeypatch.setattr(scheduler, "_notify_escalation", _raising_notify)

    with structlog.testing.capture_logs() as captured:
        await scheduler._evaluate_item(item)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await scheduler._background_tasks.drain()

    failures = [e for e in captured if e["event"] == NOTIFICATION_SEND_FAILED]
    assert len(failures) == 1
    entry: Any = failures[0]
    assert entry["log_level"] == "error"
    assert entry["owner"] == "security.timeout.scheduler"
    assert entry["intent_event"] == NOTIFICATION_ESCALATION_SEND
    assert entry["approval_id"] == "approval-x"
    assert entry["escalate_to"] == "manager"
    assert entry["error_type"] == "RuntimeError"


async def test_scheduler_stop_drains_pending_notifications() -> None:
    """Scheduler.stop() drains pending fire-and-forget notifications."""
    scheduler = ApprovalTimeoutScheduler(
        approval_store=MagicMock(spec=ApprovalStoreProtocol),
        timeout_checker=MagicMock(spec=TimeoutChecker),
        interval_seconds=60.0,
        notification_dispatcher=None,
    )

    # Deterministic blocker instead of ``asyncio.sleep(0.1)``: the
    # task stays pending until ``stop()`` cancels it, so the test
    # exercises the drain+cancel path without a real-time delay
    # that flakes under xdist.
    blocker = asyncio.Event()

    async def _blocked_until_cancelled() -> None:
        await blocker.wait()

    scheduler._background_tasks.spawn(
        _blocked_until_cancelled(),
        event=NOTIFICATION_ESCALATION_SEND,
    )
    assert scheduler._background_tasks.active_count == 1

    await scheduler.stop()
    assert scheduler._background_tasks.active_count == 0
