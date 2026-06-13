"""Tests for classification downstream sinks."""

from unittest.mock import AsyncMock

import pytest

from synthorg.budget.coordination_config import ErrorCategory
from synthorg.engine.classification.models import (
    ClassificationResult,
    ErrorFinding,
    ErrorSeverity,
)
from synthorg.engine.classification.sinks import (
    NotificationDispatcherSink,
    PerformanceTrackerSink,
)
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    NotificationCategory,
    NotificationSeverity,
)
from tests._shared.fake_clock import FakeClock


def _finding(
    *,
    category: ErrorCategory = ErrorCategory.LOGICAL_CONTRADICTION,
    severity: ErrorSeverity = ErrorSeverity.HIGH,
    description: str = "Test finding",
) -> ErrorFinding:
    return ErrorFinding(
        category=category,
        severity=severity,
        description=description,
    )


def _classification_result(
    *findings: ErrorFinding,
) -> ClassificationResult:
    categories = tuple({f.category for f in findings})
    return ClassificationResult(
        execution_id="exec-1",
        agent_id="agent-1",
        task_id="task-1",
        categories_checked=categories or (ErrorCategory.LOGICAL_CONTRADICTION,),
        findings=findings,
    )


# ── PerformanceTrackerSink ─────────────────────────────────────


@pytest.mark.unit
class TestPerformanceTrackerSink:
    """PerformanceTrackerSink records collaboration events."""

    async def test_records_event_per_finding(self) -> None:
        tracker = AsyncMock(spec=PerformanceTracker)
        tracker.record_collaboration_event = AsyncMock()
        sink = PerformanceTrackerSink(tracker=tracker)

        result = _classification_result(
            _finding(description="Contradiction A"),
            _finding(description="Contradiction B"),
        )
        await sink.on_classification(result)

        assert tracker.record_collaboration_event.await_count == 2

    async def test_no_findings_skips(self) -> None:
        tracker = AsyncMock(spec=PerformanceTracker)
        tracker.record_collaboration_event = AsyncMock()
        sink = PerformanceTrackerSink(tracker=tracker)

        result = _classification_result()
        await sink.on_classification(result)

        tracker.record_collaboration_event.assert_not_awaited()

    async def test_tracker_error_swallowed(self) -> None:
        tracker = AsyncMock(spec=PerformanceTracker)
        tracker.record_collaboration_event = AsyncMock(
            side_effect=RuntimeError("tracker down"),
        )
        sink = PerformanceTrackerSink(tracker=tracker)

        result = _classification_result(_finding())
        # Should not raise
        await sink.on_classification(result)

    async def test_memory_error_propagates(self) -> None:
        tracker = AsyncMock(spec=PerformanceTracker)
        tracker.record_collaboration_event = AsyncMock(
            side_effect=MemoryError,
        )
        sink = PerformanceTrackerSink(tracker=tracker)

        result = _classification_result(_finding())
        with pytest.raises(MemoryError):
            await sink.on_classification(result)


# ── NotificationDispatcherSink ─────────────────────────────────


@pytest.mark.unit
class TestNotificationDispatcherSink:
    """NotificationDispatcherSink dispatches notifications."""

    async def test_dispatches_high_severity(self) -> None:
        dispatcher = AsyncMock(spec=NotificationDispatcher)
        dispatcher.dispatch = AsyncMock()
        sink = NotificationDispatcherSink(dispatcher=dispatcher)

        result = _classification_result(
            _finding(severity=ErrorSeverity.HIGH),
        )
        await sink.on_classification(result)

        dispatcher.dispatch.assert_awaited_once()
        notification = dispatcher.dispatch.call_args[0][0]
        assert notification.severity == NotificationSeverity.ERROR
        assert notification.source == "engine.classification"

    async def test_filters_below_min_severity(self) -> None:
        dispatcher = AsyncMock(spec=NotificationDispatcher)
        dispatcher.dispatch = AsyncMock()
        sink = NotificationDispatcherSink(dispatcher=dispatcher)

        result = _classification_result(
            _finding(severity=ErrorSeverity.LOW),
        )
        await sink.on_classification(result)

        dispatcher.dispatch.assert_not_awaited()

    async def test_custom_min_severity(self) -> None:
        dispatcher = AsyncMock(spec=NotificationDispatcher)
        dispatcher.dispatch = AsyncMock()
        sink = NotificationDispatcherSink(
            dispatcher=dispatcher,
            min_severity=ErrorSeverity.MEDIUM,
        )

        result = _classification_result(
            _finding(severity=ErrorSeverity.MEDIUM),
        )
        await sink.on_classification(result)

        dispatcher.dispatch.assert_awaited_once()

    async def test_category_mapping(self) -> None:
        dispatcher = AsyncMock(spec=NotificationDispatcher)
        dispatcher.dispatch = AsyncMock()
        sink = NotificationDispatcherSink(dispatcher=dispatcher)

        result = _classification_result(
            _finding(
                category=ErrorCategory.AUTHORITY_BREACH_ATTEMPT,
                severity=ErrorSeverity.HIGH,
            ),
        )
        await sink.on_classification(result)

        notification = dispatcher.dispatch.call_args[0][0]
        assert notification.category == NotificationCategory.SECURITY

    async def test_no_findings_skips(self) -> None:
        dispatcher = AsyncMock(spec=NotificationDispatcher)
        dispatcher.dispatch = AsyncMock()
        sink = NotificationDispatcherSink(dispatcher=dispatcher)

        result = _classification_result()
        await sink.on_classification(result)

        dispatcher.dispatch.assert_not_awaited()

    async def test_dispatch_error_swallowed(self) -> None:
        dispatcher = AsyncMock(spec=NotificationDispatcher)
        dispatcher.dispatch = AsyncMock(
            side_effect=RuntimeError("dispatch failed"),
        )
        sink = NotificationDispatcherSink(dispatcher=dispatcher)

        result = _classification_result(_finding())
        # Should not raise
        await sink.on_classification(result)

    async def test_memory_error_propagates(self) -> None:
        dispatcher = AsyncMock(spec=NotificationDispatcher)
        dispatcher.dispatch = AsyncMock(side_effect=MemoryError)
        sink = NotificationDispatcherSink(dispatcher=dispatcher)

        result = _classification_result(_finding())
        with pytest.raises(MemoryError):
            await sink.on_classification(result)

    async def test_rate_limiter_caps_per_agent_notifications(self) -> None:
        """Sliding-window rate limiter drops excess dispatch calls."""
        dispatcher = AsyncMock(spec=NotificationDispatcher)
        dispatcher.dispatch = AsyncMock()
        fake_clock = FakeClock()
        sink = NotificationDispatcherSink(
            dispatcher=dispatcher,
            min_severity=ErrorSeverity.HIGH,
            max_events_per_window=1,
            window_seconds=60.0,
            clock=fake_clock.monotonic,
        )
        result = _classification_result(
            _finding(description="A"),
            _finding(description="B"),
            _finding(description="C"),
        )
        await sink.on_classification(result)
        assert dispatcher.dispatch.await_count == 1

    async def test_rate_limiter_refreshes_after_window(self) -> None:
        """Notifications resume once the sliding window advances."""
        dispatcher = AsyncMock(spec=NotificationDispatcher)
        dispatcher.dispatch = AsyncMock()
        fake_clock = FakeClock()
        sink = NotificationDispatcherSink(
            dispatcher=dispatcher,
            min_severity=ErrorSeverity.HIGH,
            max_events_per_window=1,
            window_seconds=10.0,
            clock=fake_clock.monotonic,
        )
        first = _classification_result(_finding(description="A"))
        await sink.on_classification(first)
        assert dispatcher.dispatch.await_count == 1

        fake_clock.advance(11.0)  # past the 10s window
        second = _classification_result(_finding(description="B"))
        await sink.on_classification(second)
        assert dispatcher.dispatch.await_count == 2

    async def test_rate_limiter_per_agent_isolation(self) -> None:
        """Different agent_ids maintain independent rate-limit counters."""
        dispatcher = AsyncMock(spec=NotificationDispatcher)
        dispatcher.dispatch = AsyncMock()
        fake_clock = FakeClock()
        sink = NotificationDispatcherSink(
            dispatcher=dispatcher,
            min_severity=ErrorSeverity.HIGH,
            max_events_per_window=1,
            window_seconds=60.0,
            clock=fake_clock.monotonic,
        )

        agent_a = ClassificationResult(
            execution_id="exec-A",
            agent_id="agent-A",
            task_id="task-A",
            categories_checked=(ErrorCategory.LOGICAL_CONTRADICTION,),
            findings=(_finding(description="A"),),
        )
        agent_b = ClassificationResult(
            execution_id="exec-B",
            agent_id="agent-B",
            task_id="task-B",
            categories_checked=(ErrorCategory.LOGICAL_CONTRADICTION,),
            findings=(_finding(description="B"),),
        )
        await sink.on_classification(agent_a)
        await sink.on_classification(agent_b)
        # Both agents were admitted because the window is per-agent.
        assert dispatcher.dispatch.await_count == 2

    async def test_notification_construction_error_swallowed(self) -> None:
        """Exceptions during Notification(...) are absorbed best-effort."""
        from unittest.mock import patch

        dispatcher = AsyncMock(spec=NotificationDispatcher)
        dispatcher.dispatch = AsyncMock()
        sink = NotificationDispatcherSink(dispatcher=dispatcher)
        result = _classification_result(_finding())
        with patch(
            "synthorg.engine.classification.sinks.Notification",
            side_effect=RuntimeError("constructor failed"),
        ):
            # Should not raise -- construction is inside the try/except.
            await sink.on_classification(result)
        dispatcher.dispatch.assert_not_awaited()
