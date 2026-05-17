"""Unit tests for the bridge-config wiring layer.

Covers the consumer-side plumbing added for #1398 / #1400:

- Positive/negative validation of the new timeout kwargs on the
  notification adapters and OAuth flows (rejects ``<= 0``).
- :meth:`AuditChainSink.set_signing_timeout_seconds` updates the
  instance attribute used by ``emit()`` and rejects invalid values.
- :class:`OAuthTokenManager.set_config_resolver` + ``start()`` resolves
  the OAuth HTTP timeout once and swaps the flow; a settings outage
  keeps the default flow in place without raising.
- :class:`WebhookEventBridge.set_config_resolver` injects the resolver
  used by the polling-loop helpers.
- :class:`JetStreamMessageBus._resolve_history_params` reads the two
  NATS history settings directly via scalar accessors and falls back
  to defaults on a resolver error.
- :class:`MessageBusBridge` throttles resolver-failure warnings to
  once-per-run-of-failures and re-arms on recovery.
- :class:`AppState.bridge_config_applied` starts ``False`` and flips
  to ``True`` exactly once via :meth:`mark_bridge_config_applied`;
  :meth:`swap_notification_dispatcher` swaps the active dispatcher and
  returns the previous instance so the caller can close its sinks.
- :func:`build_notification_dispatcher` threads timeouts from a
  ``NotificationsBridgeConfig`` into the concrete sink constructors.
- :func:`resolve_oauth_http_timeout` returns the resolver value on
  success, ``None`` on outage / no resolver.
"""

import importlib
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.bus_bridge import MessageBusBridge
from synthorg.communication.bus_protocol import MessageBus
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.engine.workflow.webhook_bridge import WebhookEventBridge
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.oauth.callback_handler import (
    resolve_oauth_http_timeout,
)
from synthorg.integrations.oauth.flows.authorization_code import (
    AuthorizationCodeFlow,
)
from synthorg.integrations.oauth.flows.client_credentials import (
    ClientCredentialsFlow,
)
from synthorg.integrations.oauth.flows.device_flow import DeviceFlow
from synthorg.integrations.oauth.token_manager import OAuthTokenManager
from synthorg.notifications.adapters.email import EmailNotificationSink
from synthorg.notifications.adapters.ntfy import NtfyNotificationSink
from synthorg.notifications.adapters.slack import SlackNotificationSink
from synthorg.notifications.config import (
    NotificationConfig,
    NotificationSinkConfig,
    NotificationSinkType,
)
from synthorg.notifications.factory import build_notification_dispatcher
from synthorg.settings.bridge_configs import NotificationsBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.sandbox.subprocess_sandbox import SubprocessSandbox
from tests._shared import mock_of

# ── Notification adapters: positive timeout validation ─────────


def _slack_factory(timeout: float) -> object:
    return SlackNotificationSink(
        webhook_url="https://hooks.slack.com/services/T/B/XYZ",
        webhook_timeout_seconds=timeout,
    )


def _ntfy_factory(timeout: float) -> object:
    return NtfyNotificationSink(
        server_url="https://ntfy.example.com",
        topic="alerts",
        webhook_timeout_seconds=timeout,
    )


def _email_factory(timeout: float) -> object:
    return EmailNotificationSink(
        host="smtp.example.com",
        port=587,
        from_addr="no-reply@example.com",
        to_addrs=("ops@example.com",),
        smtp_timeout_seconds=timeout,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("factory", "match", "bad_value"),
    [
        (_slack_factory, "webhook_timeout_seconds", 0.0),
        (_slack_factory, "webhook_timeout_seconds", -1.0),
        (_slack_factory, "webhook_timeout_seconds", float("inf")),
        (_slack_factory, "webhook_timeout_seconds", float("nan")),
        (_ntfy_factory, "webhook_timeout_seconds", 0.0),
        (_ntfy_factory, "webhook_timeout_seconds", float("inf")),
        (_email_factory, "smtp_timeout_seconds", 0.0),
        (_email_factory, "smtp_timeout_seconds", float("nan")),
    ],
    ids=[
        "slack-zero",
        "slack-negative",
        "slack-inf",
        "slack-nan",
        "ntfy-zero",
        "ntfy-inf",
        "email-zero",
        "email-nan",
    ],
)
def test_notification_adapter_rejects_invalid_timeout(
    factory: Callable[[float], Any],
    match: str,
    bad_value: float,
) -> None:
    """Every notification adapter rejects non-finite / non-positive timeouts."""
    with pytest.raises(ValueError, match=match):
        factory(bad_value)


# ── OAuth flows: positive timeout validation ──────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    ("flow_cls", "bad_value"),
    [
        (AuthorizationCodeFlow, 0.0),
        (AuthorizationCodeFlow, -1.0),
        (DeviceFlow, 0.0),
        (DeviceFlow, -5.0),
        (ClientCredentialsFlow, 0.0),
        (ClientCredentialsFlow, -1e-6),
    ],
    ids=[
        "auth-code-zero",
        "auth-code-negative",
        "device-zero",
        "device-negative",
        "client-credentials-zero",
        "client-credentials-negative",
    ],
)
def test_oauth_flow_rejects_invalid_timeout(flow_cls: type, bad_value: float) -> None:
    """Every OAuth flow rejects non-positive http_timeout_seconds."""
    with pytest.raises(ValueError, match="http_timeout_seconds"):
        flow_cls(http_timeout_seconds=bad_value)


# ── AuditChainSink.set_signing_timeout_seconds ────────────────


class TestAuditChainSinkTimeout:
    """``set_signing_timeout_seconds`` updates the live timeout."""

    @pytest.mark.unit
    def test_set_signing_timeout_seconds_updates_instance(self) -> None:
        sink_module = importlib.import_module("synthorg.observability.audit_chain.sink")
        sink = object.__new__(sink_module.AuditChainSink)
        sink._signing_timeout_seconds = 5.0
        sink.set_signing_timeout_seconds(12.5)
        assert sink._signing_timeout_seconds == 12.5

    @pytest.mark.unit
    def test_set_signing_timeout_seconds_rejects_zero(self) -> None:
        sink_module = importlib.import_module("synthorg.observability.audit_chain.sink")
        sink = object.__new__(sink_module.AuditChainSink)
        sink._signing_timeout_seconds = 5.0
        with pytest.raises(ValueError, match="signing_timeout_seconds"):
            sink.set_signing_timeout_seconds(0)


# ── OAuthTokenManager.set_config_resolver + start() ────────────


class TestOAuthTokenManagerConfigResolver:
    """Resolver injection + ``_resolve_flow_timeout`` semantics."""

    @pytest.mark.unit
    def test_set_config_resolver_stores_reference(self) -> None:
        mgr = OAuthTokenManager(catalog=MagicMock(spec=ConnectionCatalog))
        resolver = mock_of[ConfigResolver]()
        mgr.set_config_resolver(resolver)
        assert mgr._config_resolver is resolver

    @pytest.mark.unit
    async def test_resolve_flow_timeout_noop_without_resolver(self) -> None:
        mgr = OAuthTokenManager(catalog=MagicMock(spec=ConnectionCatalog))
        original = mgr._flow
        await mgr._resolve_flow_timeout()
        assert mgr._flow is original

    @pytest.mark.unit
    async def test_resolve_flow_timeout_rebuilds_flow(self) -> None:
        mgr = OAuthTokenManager(catalog=MagicMock(spec=ConnectionCatalog))
        resolver = mock_of[ConfigResolver](get_float=AsyncMock(return_value=45.0))
        mgr.set_config_resolver(resolver)
        await mgr._resolve_flow_timeout()
        # The rebuilt flow should carry the resolved timeout.
        assert mgr._flow._http_timeout_seconds == 45.0

    @pytest.mark.unit
    async def test_resolve_flow_timeout_tolerates_outage(self) -> None:
        mgr = OAuthTokenManager(catalog=MagicMock(spec=ConnectionCatalog))
        resolver = mock_of[ConfigResolver](
            get_float=AsyncMock(side_effect=RuntimeError("boom"))
        )
        mgr.set_config_resolver(resolver)
        original = mgr._flow
        # Settings outage is swallowed -- the default flow stays in place.
        await mgr._resolve_flow_timeout()
        assert mgr._flow is original

    @pytest.mark.unit
    async def test_resolve_loop_tuning_noop_without_resolver(self) -> None:
        mgr = OAuthTokenManager(catalog=MagicMock(spec=ConnectionCatalog))
        interval, threshold = mgr._interval, mgr._threshold
        await mgr._resolve_loop_tuning()
        assert mgr._interval == interval
        assert mgr._threshold == threshold

    @pytest.mark.unit
    async def test_resolve_loop_tuning_applies_resolved_values(self) -> None:
        mgr = OAuthTokenManager(catalog=MagicMock(spec=ConnectionCatalog))
        resolver = mock_of[ConfigResolver](get_int=AsyncMock(side_effect=[120, 900]))
        mgr.set_config_resolver(resolver)
        await mgr._resolve_loop_tuning()
        assert mgr._interval == 120
        assert mgr._threshold == timedelta(seconds=900)

    @pytest.mark.unit
    async def test_resolve_loop_tuning_tolerates_outage(self) -> None:
        mgr = OAuthTokenManager(catalog=MagicMock(spec=ConnectionCatalog))
        resolver = mock_of[ConfigResolver](
            get_int=AsyncMock(side_effect=RuntimeError("boom"))
        )
        mgr.set_config_resolver(resolver)
        interval, threshold = mgr._interval, mgr._threshold
        await mgr._resolve_loop_tuning()
        assert mgr._interval == interval
        assert mgr._threshold == threshold


# ── WebhookEventBridge.set_config_resolver ─────────────────────


class TestWebhookEventBridgeConfigResolver:
    @pytest.mark.unit
    def test_set_config_resolver_stores_reference(self) -> None:
        bridge = WebhookEventBridge(
            bus=MagicMock(spec=MessageBus),
            ceremony_scheduler=MagicMock(spec=CeremonyScheduler),
        )
        resolver = mock_of[ConfigResolver]()
        bridge.set_config_resolver(resolver)
        assert bridge._config_resolver is resolver


# ── MessageBusBridge resolver-fallback throttling ──────────────


class TestMessageBusBridgeFallbackLogging:
    @pytest.mark.unit
    async def test_poll_timeout_logs_once_per_failure_run(
        self,
    ) -> None:
        bus = MagicMock()
        bus.receive = AsyncMock(return_value=None)
        plugin = MagicMock()
        resolver = MagicMock(spec=ConfigResolver)
        resolver.get_float = AsyncMock(side_effect=RuntimeError("boom"))
        bridge = MessageBusBridge(bus, plugin, config_resolver=resolver)
        # First failure logs; second and third do not (flag stays set).
        await bridge._get_poll_timeout()
        await bridge._get_poll_timeout()
        assert bridge._poll_timeout_fallback_logged is True
        # Recovery clears the flag so a later failure re-logs.
        resolver.get_float = AsyncMock(return_value=1.5)
        await bridge._get_poll_timeout()
        assert bridge._poll_timeout_fallback_logged is False

    @pytest.mark.unit
    async def test_max_errors_logs_once_per_failure_run(self) -> None:
        bus = MagicMock()
        plugin = MagicMock()
        resolver = MagicMock(spec=ConfigResolver)
        resolver.get_int = AsyncMock(side_effect=RuntimeError("boom"))
        bridge = MessageBusBridge(bus, plugin, config_resolver=resolver)
        await bridge._get_max_consecutive_errors()
        assert bridge._max_errors_fallback_logged is True
        # Recover.
        resolver.get_int = AsyncMock(return_value=25)
        result = await bridge._get_max_consecutive_errors()
        assert result == 25
        assert bridge._max_errors_fallback_logged is False


# ── callback_handler.resolve_oauth_http_timeout ────────────────


class TestResolveOAuthHttpTimeout:
    @pytest.mark.unit
    async def test_returns_none_without_resolver(self) -> None:
        assert await resolve_oauth_http_timeout(None) is None

    @pytest.mark.unit
    async def test_returns_resolved_value(self) -> None:
        resolver = mock_of[ConfigResolver](get_float=AsyncMock(return_value=42.0))
        assert await resolve_oauth_http_timeout(resolver) == 42.0

    @pytest.mark.unit
    async def test_returns_none_on_outage(self) -> None:
        resolver = mock_of[ConfigResolver](
            get_float=AsyncMock(side_effect=RuntimeError("boom"))
        )
        assert await resolve_oauth_http_timeout(resolver) is None


# ── Notification factory threads timeouts ──────────────────────


class TestNotificationFactoryBridgeConfig:
    @pytest.mark.unit
    def test_slack_sink_receives_bridge_timeout(self) -> None:
        bridge_config = NotificationsBridgeConfig(
            slack_webhook_timeout_seconds=7.5,
            ntfy_webhook_timeout_seconds=10.0,
            email_smtp_timeout_seconds=10.0,
        )
        config = NotificationConfig(
            sinks=(
                NotificationSinkConfig(
                    type=NotificationSinkType.SLACK,
                    enabled=True,
                    params={"webhook_url": "https://example.com/slack-webhook"},
                ),
            ),
        )
        dispatcher = build_notification_dispatcher(config, bridge_config=bridge_config)
        slack_sinks = [
            s for s in dispatcher._sinks if isinstance(s, SlackNotificationSink)
        ]
        assert slack_sinks, "Slack sink should have been registered"
        slack = slack_sinks[0]
        # The sink stores the bridge timeout; the underlying
        # ``httpx.AsyncClient`` is created lazily inside ``start()``
        # so we assert on the stored value rather than on a
        # not-yet-constructed client.
        assert slack._webhook_timeout_seconds == 7.5

    @pytest.mark.unit
    def test_factory_without_bridge_uses_default(self) -> None:
        config = NotificationConfig(
            sinks=(
                NotificationSinkConfig(
                    type=NotificationSinkType.SLACK,
                    enabled=True,
                    params={"webhook_url": "https://example.com/slack-webhook"},
                ),
            ),
        )
        dispatcher = build_notification_dispatcher(config, bridge_config=None)
        slack_sinks = [
            s for s in dispatcher._sinks if isinstance(s, SlackNotificationSink)
        ]
        assert slack_sinks
        slack = slack_sinks[0]
        # Default is 10.0 (module default on SlackNotificationSink).
        # The sink stores it for lazy client construction in
        # ``start()``.
        assert slack._webhook_timeout_seconds == 10.0


# ── SubprocessSandbox.kill_grace_seconds ───────────────────────


class TestSubprocessSandboxKillGrace:
    @pytest.mark.unit
    def test_rejects_zero_kill_grace(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="kill_grace_seconds"):
            SubprocessSandbox(
                workspace=tmp_path,
                kill_grace_seconds=0,
            )

    @pytest.mark.unit
    def test_stores_positive_kill_grace(self, tmp_path: Path) -> None:
        sbx = SubprocessSandbox(
            workspace=tmp_path,
            kill_grace_seconds=2.5,
        )
        assert sbx._kill_grace_seconds == 2.5


# ── AppState.bridge_config_applied + swap_notification_dispatcher ──


class TestAppStateBridgeConfigFlags:
    @pytest.mark.unit
    def test_flag_starts_false_and_flips_once(self) -> None:
        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.state import AppState
        from synthorg.config.schema import RootConfig

        state = AppState(
            config=RootConfig(company_name="test"),
            approval_store=ApprovalStore(),
        )
        assert state.bridge_config_applied is False
        state.mark_bridge_config_applied()
        assert state.bridge_config_applied is True

    @pytest.mark.unit
    def test_swap_notification_dispatcher_returns_previous(self) -> None:
        from synthorg.api.approval_store import ApprovalStore
        from synthorg.api.state import AppState
        from synthorg.config.schema import RootConfig
        from synthorg.notifications.dispatcher import (
            NotificationDispatcher,
        )

        state = AppState(
            config=RootConfig(company_name="test"),
            approval_store=ApprovalStore(),
        )
        first = NotificationDispatcher(sinks=())
        second = NotificationDispatcher(sinks=())
        # Empty AppState has no dispatcher; first swap returns None.
        assert state.swap_notification_dispatcher(first) is None
        assert state.notification_dispatcher is first
        # Second swap returns the previously-installed dispatcher so
        # the caller can close its sinks without reaching back through
        # the accessor.
        assert state.swap_notification_dispatcher(second) is first
        assert state.notification_dispatcher is second
