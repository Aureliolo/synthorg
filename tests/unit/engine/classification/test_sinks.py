"""Tests for classification downstream sinks."""

import asyncio
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
    _SlidingWindowRateLimiter,
)
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
        tracker = AsyncMock()
        tracker.record_collaboration_event = AsyncMock()
        sink = PerformanceTrackerSink(tracker=tracker)

        result = _classification_result(
            _finding(description="Contradiction A"),
            _finding(description="Contradiction B"),
        )
        await sink.on_classification(result)

        assert tracker.record_collaboration_event.await_count == 2

    async def test_no_findings_skips(self) -> None:
        tracker = AsyncMock()
        tracker.record_collaboration_event = AsyncMock()
        sink = PerformanceTrackerSink(tracker=tracker)

        result = _classification_result()
        await sink.on_classification(result)

        tracker.record_collaboration_event.assert_not_awaited()

    async def test_tracker_error_swallowed(self) -> None:
        tracker = AsyncMock()
        tracker.record_collaboration_event = AsyncMock(
            side_effect=RuntimeError("tracker down"),
        )
        sink = PerformanceTrackerSink(tracker=tracker)

        result = _classification_result(_finding())
        # Should not raise
        await sink.on_classification(result)

    async def test_memory_error_propagates(self) -> None:
        tracker = AsyncMock()
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
        dispatcher = AsyncMock()
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
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        sink = NotificationDispatcherSink(dispatcher=dispatcher)

        result = _classification_result(
            _finding(severity=ErrorSeverity.LOW),
        )
        await sink.on_classification(result)

        dispatcher.dispatch.assert_not_awaited()

    async def test_custom_min_severity(self) -> None:
        dispatcher = AsyncMock()
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
        dispatcher = AsyncMock()
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
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        sink = NotificationDispatcherSink(dispatcher=dispatcher)

        result = _classification_result()
        await sink.on_classification(result)

        dispatcher.dispatch.assert_not_awaited()

    async def test_dispatch_error_swallowed(self) -> None:
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock(
            side_effect=RuntimeError("dispatch failed"),
        )
        sink = NotificationDispatcherSink(dispatcher=dispatcher)

        result = _classification_result(_finding())
        # Should not raise
        await sink.on_classification(result)

    async def test_memory_error_propagates(self) -> None:
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock(side_effect=MemoryError)
        sink = NotificationDispatcherSink(dispatcher=dispatcher)

        result = _classification_result(_finding())
        with pytest.raises(MemoryError):
            await sink.on_classification(result)

    async def test_rate_limiter_caps_per_agent_notifications(self) -> None:
        """Sliding-window rate limiter drops excess dispatch calls."""
        dispatcher = AsyncMock()
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
        dispatcher = AsyncMock()
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
        dispatcher = AsyncMock()
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

        dispatcher = AsyncMock()
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


@pytest.mark.unit
class TestSlidingWindowRateLimiterRaceConditions:
    """Race-condition regression tests.

    Concurrent ``take()`` / ``release()`` must not admit beyond
    ``max_events`` and must keep the per-key timestamp lists
    internally consistent. The async refactor (``asyncio.Lock``)
    replaced the previous sync implementation so that two awaiting
    tasks cannot both observe ``len(events) < max_events`` and admit
    beyond budget.
    """

    async def test_concurrent_take_admits_at_most_max_events(self) -> None:
        max_events = 3
        n_callers = 50
        fake_clock = FakeClock()
        limiter = _SlidingWindowRateLimiter(
            max_events=max_events,
            window_seconds=60.0,
            clock=fake_clock.monotonic,
        )
        barrier = asyncio.Barrier(n_callers)

        async def attempt_take() -> object | None:
            await barrier.wait()
            return await limiter.take("agent-A")

        # CLAUDE.md async-concurrency rule: prefer ``asyncio.TaskGroup``
        # for fan-out/fan-in. Each task body is the ``attempt_take``
        # helper; the limiter's lock guarantees per-call atomicity, so
        # no task can raise an unrelated error that would unwind the
        # group.
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(attempt_take()) for _ in range(n_callers)]
        results = [task.result() for task in tasks]
        # The non-None handles are the granted admissions.
        granted = [r for r in results if r is not None]
        assert len(granted) == max_events

    async def test_release_after_concurrent_take_reopens_slots(self) -> None:
        max_events = 5
        fake_clock = FakeClock()
        limiter = _SlidingWindowRateLimiter(
            max_events=max_events,
            window_seconds=60.0,
            clock=fake_clock.monotonic,
        )

        # Saturate the window. Capture each admission handle so we
        # can release the exact slots later.
        handles: list[object] = []
        for _ in range(max_events):
            admission = await limiter.take("agent-A")
            assert admission is not None
            handles.append(admission)
        assert await limiter.take("agent-A") is None

        # Release all admissions concurrently using their handles.
        # ``asyncio.TaskGroup`` per CLAUDE.md async-concurrency rule;
        # the bodies are infallible (limiter.release returns None on
        # missing-handle), so no task can unwind the group.
        async with asyncio.TaskGroup() as tg:
            for handle in handles:
                tg.create_task(limiter.release("agent-A", handle))

        # All slots should be free again.
        n_callers = 20
        barrier = asyncio.Barrier(n_callers)

        async def attempt_take() -> object | None:
            await barrier.wait()
            return await limiter.take("agent-A")

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(attempt_take()) for _ in range(n_callers)]
        results = [task.result() for task in tasks]
        granted = [r for r in results if r is not None]
        assert len(granted) == max_events

    async def test_release_targets_exact_admission_not_newest(self) -> None:
        """``release`` refunds the exact admission, not the newest.

        Two same-agent admissions are alive concurrently. The OLDER
        one fails after the NEWER one has already been granted; a
        ``release`` that pops the newest timestamp would refund
        the *newer* slot, leaving the failed admission counted and
        squatting on capacity.

        With ``max_events=2`` we hold two live handles at the same
        time, release the FIRST one, then assert the limiter knows
        exactly one slot reopened (a third take returns a fresh
        handle that is neither of the originals; a fourth take is
        rejected because the second admission still holds its slot).
        Setting ``max_events=1`` cannot reproduce the bug -- only one
        handle is ever live, so any release happens to land on the
        right slot by luck.
        """
        max_events = 2
        fake_clock = FakeClock()
        limiter = _SlidingWindowRateLimiter(
            max_events=max_events,
            window_seconds=60.0,
            clock=fake_clock.monotonic,
        )
        # Two same-agent admissions in-flight concurrently.
        first = await limiter.take("agent-A")
        assert first is not None
        second = await limiter.take("agent-A")
        assert second is not None
        assert second is not first
        # Window is saturated.
        assert await limiter.take("agent-A") is None

        # Release the FIRST (older) admission by handle. Under the
        # old buggy ``release`` (pop newest), this would have removed
        # ``second`` instead.
        await limiter.release("agent-A", first)

        # Exactly one slot must be free: a third take succeeds with
        # a brand-new handle.
        third = await limiter.take("agent-A")
        assert third is not None
        assert third is not first
        assert third is not second
        # The second admission is still occupying its slot, so a
        # fourth take is rejected. If the buggy refund-newest logic
        # were still in place, ``second`` would have been popped
        # earlier and this take would unexpectedly succeed.
        assert await limiter.take("agent-A") is None

        # Final disambiguation: explicitly release ``second`` and
        # confirm a slot reopens. Under the buggy "pop newest"
        # release, ``second`` would already have been popped during
        # the earlier ``release(first)`` call, so this second
        # release would be a no-op and the next take would still
        # return None. Under the correct "remove this exact handle"
        # logic, ``second`` is still in the events table and the
        # release frees its slot, so a fresh handle is granted.
        await limiter.release("agent-A", second)
        fourth = await limiter.take("agent-A")
        assert fourth is not None
        assert fourth is not first
        assert fourth is not second
        assert fourth is not third
