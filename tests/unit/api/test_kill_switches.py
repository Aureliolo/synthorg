"""Kill-switch tests for the three long-running services.

Each kill-switch follows the same pattern:

* Fail-safe to ``True`` on resolver outage (operators silence the
  surface by setting the value explicitly, never by inducing a
  settings-backend hiccup).
* Per-iteration / per-call live re-read so a flip propagates within
  one tick without a restart.

The setting-key matrix:

============================================== ==========
Setting                                        Default
============================================== ==========
``api.health_prober_enabled``                  ``true``
``api.webhook_receipt_cleanup_enabled``        ``true``
``notifications.dispatcher_enabled``           ``true``
============================================== ==========
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from synthorg.api.state import AppState
from synthorg.api.webhook_cleanup import (
    _resolve_webhook_receipt_cleanup_enabled,
    _webhook_receipt_cleanup_loop,
)
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.persistence.connection_protocol import ConnectionRepository
from synthorg.providers.health_prober import ProviderHealthProber
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state
from tests._shared.fake_clock import FakeClock


def _make_notification() -> Notification:
    return Notification(
        category=NotificationCategory.BUDGET,
        severity=NotificationSeverity.WARNING,
        title="Test notification",
        source="test",
    )


class _RecordingSink:
    """Test sink that records send() calls."""

    def __init__(self) -> None:
        self.calls: list[Notification] = []

    @property
    def sink_name(self) -> str:
        return "recording"

    async def send(self, notification: Notification) -> None:
        self.calls.append(notification)

    async def start(self) -> None:
        """No-op."""

    async def close(self) -> None:
        """No-op."""


@pytest.mark.unit
class TestNotificationDispatcherKillSwitch:
    """``notifications.dispatcher_enabled`` gates ``dispatch()`` per call."""

    async def test_dispatch_short_circuits_when_disabled(self) -> None:
        sink = _RecordingSink()
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = False
        dispatcher = NotificationDispatcher(
            sinks=(sink,),
            config_resolver=resolver,
        )

        await dispatcher.dispatch(_make_notification())

        assert sink.calls == []
        resolver.get_bool.assert_awaited_once_with(
            "notifications", "dispatcher_enabled"
        )

    async def test_dispatch_proceeds_when_enabled(self) -> None:
        sink = _RecordingSink()
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = True
        dispatcher = NotificationDispatcher(
            sinks=(sink,),
            config_resolver=resolver,
        )

        n = _make_notification()
        await dispatcher.dispatch(n)

        assert len(sink.calls) == 1
        assert sink.calls[0].id == n.id

    async def test_dispatch_fails_safe_to_enabled_on_resolver_error(
        self,
    ) -> None:
        sink = _RecordingSink()
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.side_effect = RuntimeError("settings backend down")
        dispatcher = NotificationDispatcher(
            sinks=(sink,),
            config_resolver=resolver,
        )

        await dispatcher.dispatch(_make_notification())

        assert len(sink.calls) == 1

    async def test_no_resolver_keeps_dispatcher_always_on(self) -> None:
        """Back-compat: legacy callers without a resolver never gate."""
        sink = _RecordingSink()
        dispatcher = NotificationDispatcher(sinks=(sink,))

        await dispatcher.dispatch(_make_notification())

        assert len(sink.calls) == 1

    async def test_dispatch_re_reads_resolver_per_call(self) -> None:
        """A flip from disabled to enabled propagates within one call."""
        sink = _RecordingSink()
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = False
        dispatcher = NotificationDispatcher(
            sinks=(sink,),
            config_resolver=resolver,
        )

        await dispatcher.dispatch(_make_notification())
        assert sink.calls == []

        resolver.get_bool.return_value = True
        await dispatcher.dispatch(_make_notification())

        assert len(sink.calls) == 1
        assert resolver.get_bool.await_count == 2


@pytest.mark.unit
class TestHealthProberKillSwitch:
    """``api.health_prober_enabled`` gates ``_run_loop`` per cycle."""

    @staticmethod
    def _make_prober(resolver: AsyncMock) -> ProviderHealthProber:
        from synthorg.providers.health import ProviderHealthTracker

        tracker = ProviderHealthTracker()
        return ProviderHealthProber(
            health_tracker=tracker,
            config_resolver=resolver,
        )

    async def test_resolve_enabled_returns_resolver_value(self) -> None:
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = False
        prober = self._make_prober(resolver)

        assert await prober._resolve_enabled() is False
        resolver.get_bool.assert_awaited_once_with("api", "health_prober_enabled")

    async def test_resolve_enabled_fails_safe_on_resolver_error(self) -> None:
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.side_effect = RuntimeError("settings backend down")
        prober = self._make_prober(resolver)

        assert await prober._resolve_enabled() is True

    async def test_run_loop_skips_probe_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The loop must consult the helper and skip ``_probe_all`` when False."""
        # Synchronise on the actual gate read instead of a wall-clock
        # ``asyncio.sleep`` so the test exits as soon as one iteration
        # has been observed. ``_run_loop`` blocks the rest of the
        # iteration in ``asyncio.wait_for(stop_event.wait(), timeout=...)``
        # which is interrupted immediately by ``stop_event.set()``.
        gate_consulted = asyncio.Event()

        async def _gate(*_args: object, **_kwargs: object) -> bool:
            gate_consulted.set()
            return False

        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.side_effect = _gate
        prober = self._make_prober(resolver)
        prober._interval = 1

        probe_calls = 0

        async def _fake_probe_all(self: ProviderHealthProber) -> None:
            nonlocal probe_calls
            probe_calls += 1

        # Patch on the class because ``ProviderHealthProber`` declares
        # ``__slots__`` -- per-instance attribute assignment is rejected.
        monkeypatch.setattr(ProviderHealthProber, "_probe_all", _fake_probe_all)

        task = asyncio.create_task(prober._run_loop())
        try:
            await asyncio.wait_for(gate_consulted.wait(), timeout=1.0)
        finally:
            prober._stop_event.set()
        await asyncio.wait_for(task, timeout=1.0)

        assert probe_calls == 0
        assert resolver.get_bool.await_count >= 1

    async def test_run_loop_calls_probe_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sanity: when the helper returns True, the loop probes."""
        probe_invoked = asyncio.Event()
        probe_calls = 0

        async def _fake_probe_all(self: ProviderHealthProber) -> None:
            nonlocal probe_calls
            probe_calls += 1
            probe_invoked.set()

        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = True
        prober = self._make_prober(resolver)
        prober._interval = 1

        monkeypatch.setattr(ProviderHealthProber, "_probe_all", _fake_probe_all)

        task = asyncio.create_task(prober._run_loop())
        try:
            await asyncio.wait_for(probe_invoked.wait(), timeout=1.0)
        finally:
            prober._stop_event.set()
        await asyncio.wait_for(task, timeout=1.0)

        assert probe_calls >= 1


@pytest.mark.unit
class TestWebhookCleanupKillSwitch:
    """``api.webhook_receipt_cleanup_enabled`` gates the daily sweep tick."""

    @staticmethod
    def _make_app_state(
        *,
        has_resolver: bool,
        resolver: ConfigResolver | None = None,
    ) -> AppState:
        """Lightweight stand-in for ``AppState`` carrying just the
        attributes the resolver helper reads."""
        return make_app_state(
            config_resolver=resolver if has_resolver else None,
        )

    async def test_returns_resolver_value(self) -> None:
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = False
        app_state = self._make_app_state(has_resolver=True, resolver=resolver)

        assert await _resolve_webhook_receipt_cleanup_enabled(app_state) is False
        resolver.get_bool.assert_awaited_once_with(
            "api", "webhook_receipt_cleanup_enabled"
        )

    async def test_fails_safe_to_enabled_on_resolver_error(self) -> None:
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.side_effect = RuntimeError("settings backend down")
        app_state = self._make_app_state(has_resolver=True, resolver=resolver)

        assert await _resolve_webhook_receipt_cleanup_enabled(app_state) is True

    async def test_no_resolver_falls_back_to_enabled(self) -> None:
        app_state = self._make_app_state(has_resolver=False)

        assert await _resolve_webhook_receipt_cleanup_enabled(app_state) is True

    async def test_loop_skips_sweep_when_disabled(self) -> None:
        """The loop body must short-circuit (no DB list_all) when disabled."""
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = False
        resolver.get_float.return_value = 86_400.0
        connections_repo = AsyncMock(spec=ConnectionRepository)
        connections_repo.list_items.return_value = ()
        persistence = SimpleNamespace(connections=connections_repo)
        app_state = make_app_state(
            config_resolver=resolver,
            persistence=persistence,
        )
        clock = FakeClock()

        task = asyncio.create_task(
            _webhook_receipt_cleanup_loop(app_state, clock=clock)
        )
        # Yield once so the loop hits its first ``_resolve_*`` check then
        # parks on ``clock.sleep``.
        await asyncio.sleep(0)
        await clock.advance_async(86_400.0)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Disabled => the tick body never reached ``connections.list_items``.
        connections_repo.list_items.assert_not_awaited()
        assert resolver.get_bool.await_count >= 1

    async def test_loop_runs_sweep_when_enabled(self) -> None:
        """Sanity: when the kill-switch is True the tick body fires."""
        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = True
        resolver.get_int.return_value = 30
        resolver.get_float.return_value = 86_400.0
        connections_repo = AsyncMock(spec=ConnectionRepository)
        connections_repo.list_items.return_value = ()
        persistence = SimpleNamespace(connections=connections_repo)
        app_state = make_app_state(
            config_resolver=resolver,
            persistence=persistence,
        )
        clock = FakeClock()

        task = asyncio.create_task(
            _webhook_receipt_cleanup_loop(app_state, clock=clock)
        )
        await asyncio.sleep(0)
        await clock.advance_async(86_400.0)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        connections_repo.list_items.assert_awaited()
