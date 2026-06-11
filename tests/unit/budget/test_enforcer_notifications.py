"""Regression tests: budget enforcer tracks fire-and-forget notifications.

Monthly / daily budget notifications must be tracked via
:class:`BackgroundTaskRegistry` so failures surface at ERROR with
``NOTIFICATION_SEND_FAILED``, instead of vanishing into asyncio's GC
warning stream.

``BudgetEnforcer._notify_budget_event`` already catches dispatcher
errors internally (logging ``BUDGET_NOTIFICATION_FAILED`` at WARNING),
so the registry's value here is surfacing *unexpected* failures
(e.g. import errors, malformed enum values, bugs introduced by future
refactors) that escape the internal try/except. We inject a side
effect that replaces ``_notify_budget_event`` with a raising
coroutine to simulate exactly that scenario.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
import structlog

from synthorg.budget.config import BudgetAlertConfig, BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.errors import BudgetExhaustedError, DailyLimitExceededError
from synthorg.budget.tracker import CostTracker
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability.events.notification import (
    NOTIFICATION_BUDGET_EXHAUSTED_SEND,
    NOTIFICATION_SEND_FAILED,
)

pytestmark = pytest.mark.unit


def _build_enforcer(
    *,
    cost_tracker: CostTracker,
    dispatcher: NotificationDispatcher | None,
    monthly_limit: float = 10.0,
    daily_limit: float = 1.0,
) -> BudgetEnforcer:
    cfg = BudgetConfig(
        currency="USD",
        total_monthly=monthly_limit,
        per_agent_daily_limit=daily_limit,
        alerts=BudgetAlertConfig(warn_at=50, critical_at=75, hard_stop_at=100),
    )
    return BudgetEnforcer(
        budget_config=cfg,
        cost_tracker=cost_tracker,
        notification_dispatcher=dispatcher,
    )


_NOTIFY_ERROR_MSG = "unexpected notification failure"


async def _raising_notify(*_args: object, **_kwargs: object) -> None:
    raise RuntimeError(_NOTIFY_ERROR_MSG)


async def test_monthly_hard_stop_notification_failure_logs_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing hard-stop notification logs NOTIFICATION_SEND_FAILED."""
    tracker = AsyncMock(spec=CostTracker)
    tracker.get_total_cost.return_value = 100.0  # Well over monthly_limit.
    dispatcher = AsyncMock()

    enforcer = _build_enforcer(
        cost_tracker=tracker,
        dispatcher=dispatcher,
        monthly_limit=10.0,
    )
    monkeypatch.setattr(enforcer, "_notify_budget_event", _raising_notify)

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(BudgetExhaustedError):
            await enforcer._check_monthly_hard_stop(
                enforcer._budget_config,
                agent_id="agent-1",
            )

        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await enforcer._background_tasks.drain()

    failures = [e for e in captured if e["event"] == NOTIFICATION_SEND_FAILED]
    assert len(failures) == 1
    entry = failures[0]
    assert entry["log_level"] == "error"
    assert entry["owner"] == "budget.enforcer"
    assert entry["intent_event"] == NOTIFICATION_BUDGET_EXHAUSTED_SEND
    assert entry["severity"] == "critical"
    assert entry["agent_id"] == "agent-1"
    assert entry["trigger"] == "monthly_hard_stop"
    assert entry["error_type"] == "RuntimeError"


async def test_daily_limit_notification_failure_logs_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing daily-limit notification logs NOTIFICATION_SEND_FAILED."""
    tracker = AsyncMock(spec=CostTracker)
    tracker.get_agent_cost.return_value = 5.0  # Over daily_limit.
    dispatcher = AsyncMock()

    enforcer = _build_enforcer(
        cost_tracker=tracker,
        dispatcher=dispatcher,
        daily_limit=1.0,
    )
    monkeypatch.setattr(enforcer, "_notify_budget_event", _raising_notify)

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(DailyLimitExceededError):
            await enforcer._check_daily_limit(
                enforcer._budget_config,
                agent_id="agent-2",
            )

        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await enforcer._background_tasks.drain()

    failures = [e for e in captured if e["event"] == NOTIFICATION_SEND_FAILED]
    assert len(failures) == 1
    entry = failures[0]
    assert entry["log_level"] == "error"
    assert entry["owner"] == "budget.enforcer"
    assert entry["severity"] == "warning"
    assert entry["trigger"] == "daily_agent_limit"
    assert entry["agent_id"] == "agent-2"
    assert entry["error_type"] == "RuntimeError"
