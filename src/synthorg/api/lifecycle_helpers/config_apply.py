"""Apply operator-tuned bridge config at startup.

Snapshots ``ApiBridgeConfig`` onto ``AppState``, validates cross-
setting invariants (e.g. approval-urgency ordering), populates the
sandbox image-resolution cache, rebuilds the notification dispatcher
with resolved timeouts, and applies the security-timeout scheduler
cadence. Idempotent via ``app_state.bridge_config_applied`` so a
re-entering Litestar lifespan does not churn long-lived clients.
"""

import asyncio
from typing import TYPE_CHECKING

from synthorg.notifications.factory import build_notification_dispatcher
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_BRIDGE_CONFIG_RESOLVE_FAILED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import registered_default_float

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.config.schema import RootConfig
    from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
    from synthorg.settings.bridge_configs import NotificationsBridgeConfig

logger = get_logger(__name__)


async def _validate_approval_urgency_invariant(app_state: AppState) -> None:
    """Reject startup when approval urgency thresholds violate the contract.

    ``api.approval_urgency_critical_seconds`` must be strictly less than
    ``api.approval_urgency_high_seconds`` -- a critical escalation has
    to fire sooner than a high one. Both settings are ``restart_required``,
    so the only place to enforce the cross-setting invariant is at app
    startup. Registry defaults (3600 / 14400) satisfy the invariant;
    this guard catches operator-tuned misconfigurations that the
    per-setting ``min_value`` / ``max_value`` bounds can't express.

    Resolver failures (settings backend down) are logged and the
    invariant check is skipped -- other bridge-config paths handle the
    outage independently and the built-in defaults stay safe.
    """
    try:
        critical = await app_state.config_resolver.get_float(
            SettingNamespace.API.value, "approval_urgency_critical_seconds"
        )
        high = await app_state.config_resolver.get_float(
            SettingNamespace.API.value, "approval_urgency_high_seconds"
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    if critical >= high:
        msg = (
            "Invalid approval-urgency configuration:"
            f" api.approval_urgency_critical_seconds={critical}"
            f" must be strictly less than"
            f" api.approval_urgency_high_seconds={high}."
            " A critical escalation must fire sooner than a high one."
        )
        logger.error(
            API_APP_STARTUP,
            error=msg,
            critical_seconds=critical,
            high_seconds=high,
        )
        raise ValueError(msg)


async def _apply_sandbox_image_cache(app_state: AppState) -> None:
    """Populate the sandbox / sidecar image-resolution cache from settings.

    Called once per startup so ``DockerSandboxConfig`` field defaults
    stop reading ``os.environ`` directly. ``env_var_override`` on the
    registered settings preserves the historical
    ``SYNTHORG_SANDBOX_IMAGE`` / ``SYNTHORG_SIDECAR_IMAGE`` workflow
    without bypassing the canonical DB > env > YAML > default chain.

    Resolver failures clear the cache to ``None`` so the field default
    falls through to the documented constant; whitespace-only resolver
    results are normalised to ``None`` in the caller (the setter also
    normalises, but stripping here makes the intent explicit).
    """
    from synthorg.tools.sandbox._image_resolution import (  # noqa: PLC0415
        set_resolved_sandbox_image,
        set_resolved_sidecar_image,
    )

    for setting_key, setter in (
        ("sandbox_image", set_resolved_sandbox_image),
        ("sidecar_image", set_resolved_sidecar_image),
    ):
        try:
            image_value = await app_state.config_resolver.get_str(
                SettingNamespace.TOOLS.value, setting_key
            )
        except asyncio.CancelledError:
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            setter(None)
            logger.warning(
                API_APP_STARTUP,
                setting=f"tools.{setting_key}",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        else:
            stripped = image_value.strip() if image_value is not None else None
            setter(stripped or None)


async def _apply_notification_dispatcher_config(
    app_state: AppState,
    effective_config: RootConfig | None,
) -> None:
    """Rebuild the notification dispatcher with operator-tuned timeouts.

    Reads the notifications bridge config from the resolver, then if a
    dispatcher already exists on ``app_state``, builds a fresh one with
    the resolved timeouts and swaps it in. Closes the previous
    dispatcher's sinks after the swap. Resolver outage falls through to
    a rebuild with ``bridge_config=None`` (built-in default timeouts) so
    the live dispatcher still picks up the ``config_resolver`` and the
    runtime kill-switch stays operational.
    """
    notif_bridge: NotificationsBridgeConfig | None
    try:
        notif_bridge = await app_state.config_resolver.get_notifications_bridge_config()
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            setting="notifications.bridge_config",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        notif_bridge = None
    if not (app_state.has_notification_dispatcher and effective_config is not None):
        return
    new_dispatcher = build_notification_dispatcher(
        effective_config.notifications,
        bridge_config=notif_bridge,
        config_resolver=app_state.config_resolver,
    )
    old_dispatcher = app_state.swap_notification_dispatcher(new_dispatcher)
    if old_dispatcher is None:
        return
    try:
        await old_dispatcher.aclose()
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            event_context="old_notification_dispatcher_aclose",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _apply_api_bridge_config_snapshot(app_state: AppState) -> None:
    """Snapshot ``ApiBridgeConfig`` onto ``AppState`` at startup.

    Resolves the full bridge once via
    :meth:`ConfigResolver.get_api_bridge_config` and atomically swaps
    it onto ``app_state``. On any non-fatal resolve failure the
    default ``ApiBridgeConfig()`` snapshot installed by
    ``AppState.__init__`` is retained and a single structured warning
    is emitted -- the centralised replacement for the per-request
    log-once fallback the activities controller used to carry inline.

    No-op when no resolver is wired (dev/test rigs that bypass
    ``create_app``); the default snapshot remains in place.
    """
    if not app_state.has_config_resolver:
        return
    try:
        snapshot = await app_state.config_resolver.get_api_bridge_config()
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_BRIDGE_CONFIG_RESOLVE_FAILED,
            bridge="api",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback="module_defaults",
        )
        return
    app_state.swap_api_bridge_config(snapshot)


async def _apply_ws_ticket_settings(app_state: AppState) -> None:
    """Apply the ticket-store pending-per-user limit from settings."""
    try:
        app_state.ticket_store.set_max_pending_per_user(
            await app_state.config_resolver.get_int(
                SettingNamespace.API.value,
                "ws_ticket_max_pending_per_user",
            )
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            setting="api.ws_ticket_max_pending_per_user",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _apply_ws_auth_timeout(app_state: AppState) -> None:
    """Apply the WebSocket auth-timeout seconds from settings."""
    try:
        app_state.set_ws_auth_timeout_seconds(
            await app_state.config_resolver.get_float(
                SettingNamespace.API.value,
                "ws_auth_timeout_seconds",
            )
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            setting="api.ws_auth_timeout_seconds",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _apply_ws_dos_settings(app_state: AppState) -> None:
    """Apply the per-frame WS DoS-prevention knobs from settings.

    Each setting resolves and applies independently so a single failed
    lookup (e.g. settings backend hiccup on one row) does NOT prevent
    the other two from being applied. Each falls back to its built-in
    default on failure with a structured warning so ops can see which
    knob failed.
    """
    for setting_key, setter_name in (
        ("ws_frame_timeout_seconds", "set_ws_frame_timeout_seconds"),
        ("ws_revalidation_window_seconds", "set_ws_revalidation_window_seconds"),
        ("ws_revalidation_max_failures", "set_ws_revalidation_max_failures"),
    ):
        try:
            value = await app_state.config_resolver.get_int(
                SettingNamespace.API.value,
                setting_key,
            )
            getattr(app_state, setter_name)(value)
        except asyncio.CancelledError:
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                API_APP_STARTUP,
                setting=setting_key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


async def _apply_auth_token_bytes(app_state: AppState) -> None:
    """Apply the auth-token entropy width, forcing the default on failure.

    Resolver failures force the cache back to the registered default
    so a prior successful resolve that left the cache at a non-default
    value can't persist past this branch -- the operator-facing log
    must match process state.
    """
    from synthorg.api.auth.token_size import (  # noqa: PLC0415
        _DEFAULT_AUTH_TOKEN_BYTES,
        set_auth_token_bytes,
    )

    try:
        set_auth_token_bytes(
            await app_state.config_resolver.get_int(
                SettingNamespace.SECURITY.value,
                "auth_token_bytes",
            )
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        set_auth_token_bytes(_DEFAULT_AUTH_TOKEN_BYTES)
        logger.warning(
            API_APP_STARTUP,
            setting="security.auth_token_bytes",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_bytes=_DEFAULT_AUTH_TOKEN_BYTES,
        )


async def _apply_timeout_enforcement(app_state: AppState) -> None:
    """Apply the engine timeout-enforcement flag, forcing on by default.

    Resolver failures force the cache back to ``True`` so a
    misconfigured deployment whose resolver had already returned
    ``False`` on a prior request can't keep enforcement off after
    this branch fires.
    """
    from synthorg.engine.timeout_enforcement import (  # noqa: PLC0415
        set_timeout_enforcement_enabled,
    )

    try:
        set_timeout_enforcement_enabled(
            value=await app_state.config_resolver.get_bool(
                SettingNamespace.ENGINE.value,
                "timeout_enforcement_enabled",
            )
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        set_timeout_enforcement_enabled(value=True)
        logger.warning(
            API_APP_STARTUP,
            setting="engine.timeout_enforcement_enabled",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_enabled=True,
        )


def _wire_resolver_dependents(app_state: AppState) -> None:
    """Push the active ``config_resolver`` into bridge-aware managers."""
    if app_state.oauth_token_manager is not None:
        app_state.oauth_token_manager.set_config_resolver(
            app_state.config_resolver,
        )
    if app_state.webhook_event_bridge is not None:
        app_state.webhook_event_bridge.set_config_resolver(
            app_state.config_resolver,
        )
    if app_state.escalation_notify_subscriber is not None:
        app_state.escalation_notify_subscriber.set_config_resolver(
            app_state.config_resolver,
        )
    bus = app_state.message_bus if app_state.has_message_bus else None
    if bus is not None:
        set_resolver = getattr(bus, "set_config_resolver", None)
        if callable(set_resolver):
            set_resolver(app_state.config_resolver)


async def _apply_audit_chain_signing_timeout(app_state: AppState) -> None:
    """Apply the audit-chain signing timeout to every live sink."""
    try:
        signing_timeout = await app_state.config_resolver.get_float(
            SettingNamespace.OBSERVABILITY.value,
            "audit_chain_signing_timeout_seconds",
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            setting="observability.audit_chain_signing_timeout_seconds",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return

    from synthorg.observability.audit_chain.sink import (  # noqa: PLC0415
        AuditChainSink,
    )
    from synthorg.observability.startup_wiring import (  # noqa: PLC0415
        _iter_logging_handlers,
    )

    for handler in _iter_logging_handlers():
        if not isinstance(handler, AuditChainSink):
            continue
        try:
            handler.set_signing_timeout_seconds(signing_timeout)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                API_APP_STARTUP,
                setting="observability.audit_chain_signing_timeout_seconds",
                phase="apply_to_handler",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


async def _apply_bridge_config(
    app_state: AppState,
    effective_config: RootConfig | None,
) -> None:
    """Apply operator-tuned API bridge settings during startup.

    Idempotent via ``app_state.bridge_config_applied`` so a re-entering
    Litestar lifespan (shared-app test fixtures, multi-lifespan runs)
    does not churn httpx/SMTP clients or rebuild the OAuth flow.
    """
    if not app_state.has_config_resolver or app_state.bridge_config_applied:
        return

    await _validate_approval_urgency_invariant(app_state)
    await _apply_api_bridge_config_snapshot(app_state)
    await _apply_ws_ticket_settings(app_state)
    await _apply_ws_auth_timeout(app_state)
    await _apply_ws_dos_settings(app_state)
    await _apply_auth_token_bytes(app_state)
    await _apply_timeout_enforcement(app_state)
    await _apply_sandbox_image_cache(app_state)
    _wire_resolver_dependents(app_state)
    await _apply_audit_chain_signing_timeout(app_state)
    await _apply_notification_dispatcher_config(app_state, effective_config)

    app_state.mark_bridge_config_applied()


async def _apply_security_timeout_interval(
    app_state: AppState,
    scheduler: ApprovalTimeoutScheduler | None,
) -> None:
    """Apply ``security.timeout_check_interval_seconds`` at startup.

    The scheduler is bootstrapped with the registry default at app
    construction time before persistence connects. Once the resolver
    is wired (after ``_apply_bridge_config``), pull the operator-tuned
    value via the canonical DB > env > YAML > default chain and call
    ``scheduler.reschedule(...)`` so the configured cadence takes
    effect on the next loop tick.

    Resolver outage falls back to the *current* scheduler interval
    (the registry default the scheduler was bootstrapped with). The
    fail-safe-on-outage rule from the kill-switch idiom applies in
    spirit: leaving the scheduler running with the bootstrap default
    is safer than stopping it on a settings-backend hiccup.
    """
    if scheduler is None or not app_state.has_config_resolver:
        return
    fallback = registered_default_float(
        SettingNamespace.SECURITY.value,
        "timeout_check_interval_seconds",
    )
    try:
        interval = await app_state.config_resolver.get_float(
            SettingNamespace.SECURITY.value,
            "timeout_check_interval_seconds",
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            setting="security.timeout_check_interval_seconds",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_seconds=fallback,
        )
        return
    try:
        scheduler.reschedule(interval)
    except ValueError as exc:
        logger.warning(
            API_APP_STARTUP,
            setting="security.timeout_check_interval_seconds",
            value=interval,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
