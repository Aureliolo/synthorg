"""Unit tests for BackgroundTaskRegistry."""

import asyncio
import contextlib
import logging
from collections.abc import Callable, MutableMapping
from typing import Any

import pytest
import structlog.testing
from pydantic import JsonValue

from synthorg.observability import get_logger
from synthorg.observability.background_tasks import (
    BackgroundTaskRegistry,
    log_task_exceptions,
)
from synthorg.observability.events.async_task import (
    BACKGROUND_TASKS_DRAIN_TIMEOUT,
)
from synthorg.observability.events.notification import NOTIFICATION_SEND_FAILED

pytestmark = pytest.mark.unit


async def _noop() -> None:
    return None


async def _raiser(exc: BaseException) -> None:
    raise exc


async def _block_until_set(blocker: asyncio.Event) -> None:
    """Block until *blocker* is set, or the task is cancelled.

    Used as a deterministic replacement for ``asyncio.sleep`` in
    timing-sensitive tests: the caller controls exactly when the
    coroutine completes or gets cancelled, eliminating real-time
    dependencies and xdist flakiness.
    """
    await blocker.wait()


async def test_spawn_tracks_task_and_discards_on_success() -> None:
    registry = BackgroundTaskRegistry(owner="test.owner")
    task = registry.spawn(_noop(), event="test.intent")
    assert registry.active_count == 1
    await task
    await asyncio.sleep(0)
    assert registry.active_count == 0


async def test_failed_task_logs_notification_send_failed(
    captured_logs: list[MutableMapping[str, JsonValue]],
) -> None:
    registry = BackgroundTaskRegistry(owner="test.owner")
    _ = registry.spawn(
        _raiser(ValueError("notify failed")),
        event="test.intent",
        severity="critical",
        agent_id="agent-42",
    )
    # Allow task body + done-callback to run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert registry.active_count == 0
    failures = [
        entry for entry in captured_logs if entry["event"] == NOTIFICATION_SEND_FAILED
    ]
    assert len(failures) == 1
    entry = failures[0]
    assert entry["log_level"] == "error"
    assert entry["owner"] == "test.owner"
    assert entry["intent_event"] == "test.intent"
    assert entry["severity"] == "critical"
    assert entry["agent_id"] == "agent-42"
    assert entry["error_type"] == "ValueError"


async def test_cancelled_task_does_not_log_failure(
    captured_logs: list[MutableMapping[str, JsonValue]],
) -> None:
    registry = BackgroundTaskRegistry(owner="test.owner")
    blocker = asyncio.Event()
    task = registry.spawn(_block_until_set(blocker), event="test.intent")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert registry.active_count == 0
    failures = [
        entry for entry in captured_logs if entry["event"] == NOTIFICATION_SEND_FAILED
    ]
    assert not failures


async def test_drain_waits_for_pending_tasks() -> None:
    registry = BackgroundTaskRegistry(owner="test.owner")
    blocker = asyncio.Event()
    _ = registry.spawn(_block_until_set(blocker), event="test.intent")
    _ = registry.spawn(_block_until_set(blocker), event="test.intent")
    assert registry.active_count == 2
    # Release both blockers so drain has something to wait on
    # (rather than a timing-dependent 50ms sleep).
    blocker.set()
    await registry.drain(timeout_sec=1.0)
    assert registry.active_count == 0


async def test_drain_cancels_on_timeout(
    captured_logs: list[MutableMapping[str, JsonValue]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = BackgroundTaskRegistry(owner="test.owner")
    blocker = asyncio.Event()  # Never set -- task is stuck forever.
    task = registry.spawn(_block_until_set(blocker), event="test.intent")

    # Drive the timeout branch deterministically: stub the FIRST
    # ``asyncio.wait`` call so the first drain phase returns "all
    # pending" without a real 50ms wall-clock wait, then delegate
    # subsequent calls back to the real implementation so the
    # post-cancellation cleanup phase still observes tasks finishing.
    # Keeps the test off the CI scheduler's timing while preserving
    # full drain semantics.
    original_wait = asyncio.wait
    call_count = 0

    async def _wait_shim(
        tasks: set[asyncio.Task[object]],
        *,
        timeout: float | None = None,  # noqa: ASYNC109 - mirrors asyncio.wait
        # ``**kwargs`` forwards verbatim to the real ``asyncio.wait``
        # (typed ``return_when: str``); ``Any`` is the correct
        # stdlib-passthrough type here.
        **kwargs: Any,
    ) -> tuple[set[asyncio.Task[object]], set[asyncio.Task[object]]]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return (set(), set(tasks))
        return await original_wait(tasks, timeout=timeout, **kwargs)

    monkeypatch.setattr("asyncio.wait", _wait_shim)
    try:
        await registry.drain(timeout_sec=0.05)
    finally:
        monkeypatch.setattr("asyncio.wait", original_wait)

    # Allow the cancellation to settle.
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    # Registry must drop the task from its pending set after
    # the done-callback fires -- otherwise a timed-out drain
    # would leak references for the life of the registry.
    assert registry.active_count == 0
    warn_entries = [
        entry
        for entry in captured_logs
        if entry["event"] == BACKGROUND_TASKS_DRAIN_TIMEOUT
    ]
    assert len(warn_entries) == 1
    entry = warn_entries[0]
    assert entry["log_level"] == "warning"
    assert entry["owner"] == "test.owner"
    assert entry["pending_count"] == 1
    assert entry["timeout_sec"] == 0.05


async def test_drain_is_noop_when_no_tasks() -> None:
    registry = BackgroundTaskRegistry(owner="test.owner")
    await asyncio.wait_for(registry.drain(timeout_sec=0.01), timeout=0.2)


async def test_no_task_exception_warning_on_failed_task(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """asyncio must not emit 'Task exception was never retrieved'.

    A bare ``asyncio.create_task`` whose exception is never observed
    triggers that warning. Our registry's done-callback calls
    ``task.exception()`` before discarding the reference, so asyncio
    considers the exception retrieved and keeps quiet.
    """
    registry = BackgroundTaskRegistry(owner="test.owner")
    caplog.set_level(logging.WARNING)
    _ = registry.spawn(_raiser(RuntimeError("boom")), event="test.intent")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not any(
        "Task exception was never retrieved" in (r.getMessage() or "")
        for r in caplog.records
    )


class TestLogTaskExceptions:
    """``log_task_exceptions`` factory: callback for long-lived tasks."""

    async def test_logs_uncancelled_exception(self) -> None:
        logger = get_logger("test.log_task_exceptions")
        with structlog.testing.capture_logs() as events:
            task = asyncio.create_task(_raiser(ValueError("broken")))
            task.add_done_callback(
                log_task_exceptions(logger, "test.event", subsystem="loop"),
            )
            with contextlib.suppress(ValueError):
                await task
            await asyncio.sleep(0)
        matched = [e for e in events if e.get("event") == "test.event"]
        assert matched
        assert matched[0]["log_level"] == "warning"
        assert matched[0].get("subsystem") == "loop"
        assert matched[0].get("error_type") == "ValueError"

    async def test_ignores_cancelled_task(self) -> None:
        logger = get_logger("test.log_task_exceptions")
        started = asyncio.Event()

        async def _runner() -> None:
            started.set()
            await asyncio.Event().wait()

        with structlog.testing.capture_logs() as events:
            task = asyncio.create_task(_runner())
            task.add_done_callback(log_task_exceptions(logger, "test.event"))
            await started.wait()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
        assert not any(e.get("event") == "test.event" for e in events)

    async def test_success_logs_nothing(self) -> None:
        logger = get_logger("test.log_task_exceptions")
        with structlog.testing.capture_logs() as events:
            task = asyncio.create_task(_noop())
            task.add_done_callback(log_task_exceptions(logger, "test.event"))
            await task
            await asyncio.sleep(0)
        assert not any(e.get("event") == "test.event" for e in events)

    @pytest.mark.parametrize(
        ("exc_factory", "expected_level", "expected_error_type"),
        [
            (lambda: ValueError("bad"), "warning", "ValueError"),
            (lambda: RuntimeError("boom"), "warning", "RuntimeError"),
            (lambda: MemoryError("oom"), "critical", "MemoryError"),
            (lambda: RecursionError("deep"), "critical", "RecursionError"),
        ],
    )
    async def test_severity_to_log_level_mapping(
        self,
        exc_factory: Callable[[], BaseException],
        expected_level: str,
        expected_error_type: str,
    ) -> None:
        """Exception class controls the emitted log level.

        Resource-exhaustion errors (``MemoryError``/``RecursionError``)
        escalate to CRITICAL + the event-loop exception handler; every
        other uncancelled exception logs at WARNING.  The contract is
        load-bearing for monitoring alerts, so pin it per-level.
        """
        logger = get_logger("test.log_task_exceptions")
        exc = exc_factory()
        with structlog.testing.capture_logs() as events:
            task = asyncio.create_task(_raiser(exc))
            task.add_done_callback(log_task_exceptions(logger, "test.event"))
            with contextlib.suppress(type(exc)):
                await task
            await asyncio.sleep(0)
        matched = [e for e in events if e.get("event") == "test.event"]
        assert matched, f"expected a log for {expected_error_type}"
        assert matched[0]["log_level"] == expected_level
        assert matched[0].get("error_type") == expected_error_type

    async def test_context_frozen_after_registration(self) -> None:
        """Mutating ``context`` after registering the callback is a no-op."""
        logger = get_logger("test.log_task_exceptions")
        context: dict[str, JsonValue] = {"channel": "alpha"}
        with structlog.testing.capture_logs() as events:
            task = asyncio.create_task(_raiser(ValueError("x")))
            task.add_done_callback(
                log_task_exceptions(logger, "test.event", **context),
            )
            context["channel"] = "mutated-after"
            with contextlib.suppress(ValueError):
                await task
            await asyncio.sleep(0)
        matched = [e for e in events if e.get("event") == "test.event"]
        assert matched
        assert matched[0].get("channel") == "alpha"  # not "mutated-after"
