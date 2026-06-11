"""Tests for QuotaPoller lifecycle, polling, alerting, and cooldown."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.budget.quota import QuotaSnapshot, QuotaWindow
from synthorg.budget.quota_poller import QuotaPoller
from synthorg.budget.quota_poller_config import QuotaAlertThresholds, QuotaPollerConfig
from synthorg.notifications.dispatcher import NotificationDispatcher
from tests._shared.fake_clock import FakeClock


def _snapshot(  # noqa: PLR0913
    provider: str = "test-provider",
    *,
    window: QuotaWindow = QuotaWindow.PER_MINUTE,
    requests_used: int = 0,
    requests_limit: int = 0,
    tokens_used: int = 0,
    tokens_limit: int = 0,
) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider_name=provider,
        window=window,
        requests_used=requests_used,
        requests_limit=requests_limit,
        tokens_used=tokens_used,
        tokens_limit=tokens_limit,
        captured_at=datetime(2026, 4, 1, tzinfo=UTC),
    )


def _make_poller(
    snapshots: dict[str, tuple[QuotaSnapshot, ...]] | None = None,
    *,
    config: QuotaPollerConfig | None = None,
    notification_dispatcher: NotificationDispatcher | None = None,
    clock: FakeClock | None = None,
) -> tuple[QuotaPoller, AsyncMock]:
    tracker = AsyncMock()
    tracker.get_all_snapshots = AsyncMock(return_value=snapshots or {})

    return QuotaPoller(
        quota_tracker=tracker,
        config=config or QuotaPollerConfig(enabled=True),
        notification_dispatcher=notification_dispatcher,
        clock=clock,
    ), tracker


@pytest.mark.unit
class TestPollOnce:
    """poll_once dispatches alerts when thresholds are crossed."""

    async def test_no_snapshots_no_alert(self) -> None:
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        poller, _ = _make_poller({}, notification_dispatcher=dispatcher)
        await poller.poll_once()
        dispatcher.dispatch.assert_not_called()

    async def test_usage_below_warn_no_alert(self) -> None:
        """50% usage should not trigger any alert."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        snap = _snapshot(requests_used=50, requests_limit=100)
        poller, _ = _make_poller(
            {"test-provider": (snap,)},
            notification_dispatcher=dispatcher,
        )
        await poller.poll_once()
        dispatcher.dispatch.assert_not_called()

    async def test_warn_threshold_triggers_warning(self) -> None:
        """80% usage triggers WARNING when warn_pct=80."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        config = QuotaPollerConfig(
            enabled=True,
            alert_thresholds=QuotaAlertThresholds(warn_pct=80.0, critical_pct=95.0),
        )
        snap = _snapshot(requests_used=80, requests_limit=100)
        poller, _ = _make_poller(
            {"test-provider": (snap,)},
            config=config,
            notification_dispatcher=dispatcher,
        )
        await poller.poll_once()
        dispatcher.dispatch.assert_called_once()

    async def test_critical_threshold_triggers_critical(self) -> None:
        """95% usage triggers CRITICAL when critical_pct=95."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        config = QuotaPollerConfig(
            enabled=True,
            alert_thresholds=QuotaAlertThresholds(warn_pct=80.0, critical_pct=95.0),
        )
        snap = _snapshot(requests_used=95, requests_limit=100)
        poller, _ = _make_poller(
            {"test-provider": (snap,)},
            config=config,
            notification_dispatcher=dispatcher,
        )
        await poller.poll_once()
        call_args = dispatcher.dispatch.call_args_list
        assert len(call_args) == 1
        notification = call_args[0].args[0]
        assert notification.severity.value == "critical"

    async def test_warn_notification_severity(self) -> None:
        """80% usage produces WARNING severity notification."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        snap = _snapshot(requests_used=80, requests_limit=100)
        poller, _ = _make_poller(
            {"test-provider": (snap,)},
            notification_dispatcher=dispatcher,
        )
        await poller.poll_once()
        notification = dispatcher.dispatch.call_args.args[0]
        assert notification.severity.value == "warning"

    async def test_token_usage_triggers_alert(self) -> None:
        """Token usage at 80% triggers alert (only token limit set)."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        snap = _snapshot(tokens_used=80, tokens_limit=100)
        poller, _ = _make_poller(
            {"test-provider": (snap,)},
            notification_dispatcher=dispatcher,
        )
        await poller.poll_once()
        dispatcher.dispatch.assert_called_once()

    async def test_max_of_request_and_token_pct_used(self) -> None:
        """Usage pct = max(request_pct, token_pct).

        requests=50%, tokens=90% -- max=90% should govern the severity.
        With critical_pct=85, 90% should fire critical (not just warning
        from 50% requests).
        """
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        config = QuotaPollerConfig(
            enabled=True,
            alert_thresholds=QuotaAlertThresholds(warn_pct=70.0, critical_pct=85.0),
        )
        # requests at 50% (below warn), tokens at 90% -> max = 90% -> critical
        snap = _snapshot(
            requests_used=50,
            requests_limit=100,
            tokens_used=90,
            tokens_limit=100,
        )
        poller, _ = _make_poller(
            {"test-provider": (snap,)},
            config=config,
            notification_dispatcher=dispatcher,
        )
        await poller.poll_once()
        notification = dispatcher.dispatch.call_args.args[0]
        assert notification.severity.value == "critical"

    async def test_unlimited_dimension_skipped(self) -> None:
        """Dimensions with limit=0 are skipped (unlimited)."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        snap = _snapshot(requests_used=1000, requests_limit=0)
        poller, _ = _make_poller(
            {"test-provider": (snap,)},
            notification_dispatcher=dispatcher,
        )
        await poller.poll_once()
        dispatcher.dispatch.assert_not_called()

    async def test_no_dispatcher_no_crash(self) -> None:
        """poll_once without dispatcher runs without error."""
        snap = _snapshot(requests_used=90, requests_limit=100)
        poller, _ = _make_poller({"test-provider": (snap,)})
        await poller.poll_once()

    async def test_multiple_providers_independently_tracked(self) -> None:
        """Each provider+window is evaluated independently."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        snap_a = _snapshot("provider-a", requests_used=90, requests_limit=100)
        snap_b = _snapshot("provider-b", requests_used=10, requests_limit=100)
        poller, _ = _make_poller(
            {"provider-a": (snap_a,), "provider-b": (snap_b,)},
            notification_dispatcher=dispatcher,
        )
        await poller.poll_once()
        dispatcher.dispatch.assert_called_once()


@pytest.mark.unit
class TestCooldown:
    """Cooldown suppresses duplicate alerts."""

    async def test_cooldown_suppresses_duplicate(self) -> None:
        """Second poll within cooldown should not dispatch again."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        snap = _snapshot(requests_used=90, requests_limit=100)
        poller, _tracker = _make_poller(
            {"test-provider": (snap,)},
            config=QuotaPollerConfig(
                enabled=True,
                cooldown_seconds=300.0,
            ),
            notification_dispatcher=dispatcher,
        )
        await poller.poll_once()
        await poller.poll_once()
        dispatcher.dispatch.assert_called_once()

    async def test_cooldown_expiry_allows_re_alert(self) -> None:
        """After cooldown expires the alert fires again."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        snap = _snapshot(requests_used=90, requests_limit=100)

        clock = FakeClock()
        poller, _ = _make_poller(
            {"test-provider": (snap,)},
            config=QuotaPollerConfig(
                enabled=True,
                cooldown_seconds=300.0,
            ),
            notification_dispatcher=dispatcher,
            clock=clock,
        )
        await poller.poll_once()
        clock.advance(301.0)
        await poller.poll_once()

        assert dispatcher.dispatch.call_count == 2

    async def test_different_providers_independent_cooldown(self) -> None:
        """Cooldown for provider-a does not block provider-b."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        snap_a = _snapshot("provider-a", requests_used=90, requests_limit=100)
        snap_b = _snapshot("provider-b", requests_used=90, requests_limit=100)
        poller, _ = _make_poller(
            {"provider-a": (snap_a,), "provider-b": (snap_b,)},
            config=QuotaPollerConfig(enabled=True, cooldown_seconds=300.0),
            notification_dispatcher=dispatcher,
        )
        await poller.poll_once()
        # Both fired independently on first poll
        assert dispatcher.dispatch.call_count == 2


@pytest.mark.unit
class TestTrackerError:
    """poll_once is resilient to tracker failures."""

    async def test_tracker_error_logged_not_raised(self) -> None:
        """Tracker raising exception is caught and logged."""
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()

        from synthorg.budget.quota_poller import QuotaPoller

        tracker = AsyncMock()
        tracker.get_all_snapshots = AsyncMock(side_effect=RuntimeError("tracker down"))
        poller = QuotaPoller(
            quota_tracker=tracker,
            config=QuotaPollerConfig(enabled=True),
            notification_dispatcher=dispatcher,
        )
        # Should not raise
        await poller.poll_once()
        dispatcher.dispatch.assert_not_called()


@pytest.mark.unit
class TestStartStop:
    """QuotaPoller start/stop lifecycle."""

    async def test_start_creates_background_task(self) -> None:
        poller, _ = _make_poller()
        await poller.start()
        assert poller._task is not None
        await poller.stop()

    async def test_start_idempotent(self) -> None:
        poller, _ = _make_poller()
        await poller.start()
        original = poller._task
        await poller.start()
        assert poller._task is original
        await poller.stop()

    async def test_stop_cancels_task(self) -> None:
        poller, _ = _make_poller()
        await poller.start()
        await poller.stop()
        assert poller._task is None or poller._task.done()
