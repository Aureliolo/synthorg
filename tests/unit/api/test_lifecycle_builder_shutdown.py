"""Regression tests for the bounded janitor-task shutdown helper.

The lifecycle builder owns three long-lived janitor loops
(``_ticket_cleanup_task``, ``_audit_retention_task``,
``_webhook_cleanup_task``). Shutdown must remain bounded even when a
task body shields ``CancelledError`` -- otherwise the orchestrator's
``graceful_shutdown`` (75 s in ``api/server.py``) is overrun and the
process is SIGKILLed mid-teardown.

These tests exercise ``_cancel_with_timeout`` directly because the
helper is the testable unit; full lifecycle integration is covered
elsewhere.
"""

import asyncio
import contextlib

import pytest
import structlog.testing

from synthorg.api.lifecycle_builder import _cancel_with_timeout


async def _absorb_one_cancel() -> None:
    """Block, absorb one ``CancelledError``, then block again.

    Models a janitor body that catches the first cancellation
    (third-party callee with ``except Exception``) but accepts the
    second one. The first cancel comes from
    ``_cancel_with_timeout``'s explicit ``task.cancel()``; the
    second comes from ``asyncio.wait_for`` on timeout, so the task
    completes cleanly after the helper returns. Both waits block on an
    unset ``Event`` rather than a long sleep so the task is guaranteed
    still pending when the helper's timeout fires.
    """
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.Event().wait()
    # The second cancel arrives here and propagates up, ending the task.
    await asyncio.Event().wait()


@pytest.mark.unit
class TestCancelWithTimeout:
    """Bounded cancel-and-await for shielded janitor tasks."""

    async def test_completes_quickly_on_normal_cancellation(self) -> None:
        """A cooperative task returns inside the timeout window."""

        async def cooperative() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return

        task = asyncio.create_task(cooperative(), name="cooperative")
        await asyncio.sleep(0)

        await _cancel_with_timeout(task, service="ticket_cleanup", timeout=2.0)
        assert task.done()
        assert task.exception() is None

    async def test_timeout_resolves_shielded_task(self) -> None:
        """The helper returns and ends a body that shields the first cancel.

        ``_absorb_one_cancel`` blocks on an unset ``Event`` forever, so a
        helper that returned with the task ``done`` proves the bounded
        ``asyncio.wait_for`` fired and re-cancelled the task rather than
        blocking on the never-completing wait.
        """
        task = asyncio.create_task(_absorb_one_cancel(), name="shielded")
        await asyncio.sleep(0)

        await _cancel_with_timeout(task, service="ticket_cleanup", timeout=0.1)

        assert task.done()
        assert task.cancelled()

    async def test_timeout_emits_shutdown_timeout_event(self) -> None:
        """``API_APP_SHUTDOWN_TIMEOUT`` fires at ERROR with the service label."""
        task = asyncio.create_task(_absorb_one_cancel(), name="shielded-event")
        await asyncio.sleep(0)

        with structlog.testing.capture_logs() as captured:
            await _cancel_with_timeout(
                task,
                service="audit_retention",
                timeout=0.05,
            )

        timeout_logs = [
            entry
            for entry in captured
            if entry.get("event") == "api.app.shutdown.timeout"
        ]
        assert len(timeout_logs) == 1, captured
        entry = timeout_logs[0]
        assert entry["log_level"] == "error"
        assert entry["service"] == "audit_retention"
        assert entry["timeout_seconds"] == 0.05
        assert entry["error_type"] == "TimeoutError"

    @pytest.mark.parametrize(
        "service",
        ["ticket_cleanup", "audit_retention", "webhook_cleanup"],
    )
    async def test_supports_all_three_cleanup_services(self, service: str) -> None:
        """Each janitor task can be cancelled with its own service label."""

        async def cooperative() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return

        task = asyncio.create_task(cooperative(), name=service)
        await asyncio.sleep(0)
        await _cancel_with_timeout(task, service=service, timeout=2.0)
        assert task.done()
