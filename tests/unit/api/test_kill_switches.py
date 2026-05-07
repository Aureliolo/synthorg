"""Kill-switch tests for the three long-running services (issue #1776).

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

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.api.state import AppState
from synthorg.api.webhook_cleanup import (
    _resolve_webhook_receipt_cleanup_enabled,
)
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from synthorg.providers.health_prober import ProviderHealthProber
from synthorg.settings.resolver import ConfigResolver


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

    async def test_set_config_resolver_enables_runtime_gate(self) -> None:
        sink = _RecordingSink()
        dispatcher = NotificationDispatcher(sinks=(sink,))

        # First dispatch with no resolver -> always-on path.
        await dispatcher.dispatch(_make_notification())
        assert len(sink.calls) == 1

        resolver = AsyncMock(spec=ConfigResolver)
        resolver.get_bool.return_value = False
        dispatcher.set_config_resolver(resolver)

        # Second dispatch with kill-switch flipped -> short-circuits.
        await dispatcher.dispatch(_make_notification())
        assert len(sink.calls) == 1


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
        return cast(
            AppState,
            SimpleNamespace(
                has_config_resolver=has_resolver,
                config_resolver=resolver,
            ),
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
