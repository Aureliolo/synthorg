# module-kind: orchestrator
"""Apply operator-tuned bridge config at startup.

Snapshots ``ApiBridgeConfig`` onto ``AppState``, validates cross-
setting invariants (e.g. approval-urgency ordering), populates the
sandbox image-resolution cache, rebuilds the notification dispatcher
with resolved timeouts, and applies the security-timeout scheduler
cadence. Idempotent via ``app_state.bridge_config.applied`` so a
re-entering Litestar lifespan does not churn long-lived clients.
"""

import asyncio
from collections.abc import Callable

from synthorg.api.api_core_state import ticket_store_of
from synthorg.api.lifecycle_helpers.bridge_snapshots import (
    _apply_api_bridge_config_snapshot,
    _apply_memory_bridge_config_snapshot,
    _apply_observability_bridge_config_snapshot,
    _apply_tools_bridge_config_snapshot,
    _apply_workers_bridge_config_snapshot,
)
from synthorg.api.lifecycle_helpers.image_cache_apply import (
    _apply_fine_tune_image_cache,
    _apply_sandbox_image_cache,
)
from synthorg.api.state import AppState
from synthorg.communication.state import CommunicationStateSlice
from synthorg.config.schema import RootConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.notifications.factory import build_notification_dispatcher
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.security.timeout.scheduler import ApprovalTimeoutScheduler
from synthorg.settings.bridge_configs import (
    NotificationsBridgeConfig,
    ObservabilityBridgeConfig,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import registered_default_float
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)


async def _validate_approval_urgency_invariant(app_state: AppState) -> None:
    """Reject startup when approval urgency thresholds violate the contract.

    ``api.approval_urgency_critical_seconds`` must be strictly less than
    ``api.approval_urgency_high_seconds`` -- a critical escalation has
    to fire sooner than a high one. A later edit is caught where the pair is
    read: ``_resolve_urgency_thresholds`` validates it per read and falls back
    to the defaults when it is inverted. This catches a pair already stored
    inverted, so startup refuses rather than silently running on defaults an
    operator believes they have replaced. Registry
    defaults (3600 / 14400) satisfy the invariant; the guard catches
    operator-tuned misconfigurations that the per-setting ``min_value`` /
    ``max_value`` bounds cannot express.

    Resolver failures (settings backend down) are logged and the
    invariant check is skipped -- other bridge-config paths handle the
    outage independently and the built-in defaults stay safe.

    Raises:
        CancelledError: Raised on the corresponding failure path.
        ValueError: Raised on the corresponding failure path.
    """
    try:
        critical = await config_resolver_of(app_state).get_float(
            SettingNamespace.API.value, "approval_urgency_critical_seconds"
        )
        high = await config_resolver_of(app_state).get_float(
            SettingNamespace.API.value, "approval_urgency_high_seconds"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
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

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    notif_bridge: NotificationsBridgeConfig | None
    resolver = config_resolver_of(app_state)
    try:
        notif_bridge = await resolver.get_notifications_bridge_config()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            setting="notifications.bridge_config",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        notif_bridge = None
    if not (
        app_state.slice(NotificationsStateSlice).dispatcher is not None
        and effective_config is not None
    ):
        return
    new_dispatcher = build_notification_dispatcher(
        effective_config.notifications,
        bridge_config=notif_bridge,
        config_resolver=config_resolver_of(app_state),
        connection_catalog=app_state.slice(IntegrationsStateSlice).connection_catalog,
        thread_registry=app_state.slice(IntegrationsStateSlice).inbound_thread_registry,
    )
    # Start BEFORE swapping so a failed start leaves the live (already
    # running) dispatcher in place rather than installing a built-but-
    # unstarted one whose sinks silently drop events. Mirrors the boot
    # path in ``auto_wire.py`` / ``lifecycle_assembly.py``. The ``finally``
    # aclose covers BOTH a non-critical start failure AND a
    # ``CancelledError`` (SIGTERM mid-start): either way a partially-started
    # dispatcher's sinks (HTTP / SMTP sessions) are released rather than
    # leaked.
    start_ok = False
    try:
        await new_dispatcher.start()
        start_ok = True
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            event_context="new_notification_dispatcher_start",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    finally:
        if not start_ok:
            try:
                await new_dispatcher.aclose()
            except Exception as close_exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(close_exc)
                logger.warning(
                    API_APP_STARTUP,
                    event_context="new_notification_dispatcher_aclose_after_start_failure",
                    error_type=type(close_exc).__name__,
                    error=safe_error_description(close_exc),
                )
    if not start_ok:
        return
    old_dispatcher = app_state.swap_notification_dispatcher(new_dispatcher)
    if old_dispatcher is None:
        return
    try:
        await old_dispatcher.aclose()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            event_context="old_notification_dispatcher_aclose",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _apply_ws_ticket_settings(app_state: AppState) -> None:
    """Apply the ticket-store pending-per-user limit from settings.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    try:
        ticket_store_of(app_state).set_max_pending_per_user(
            await config_resolver_of(app_state).get_int(
                SettingNamespace.API.value,
                "ws_ticket_max_pending_per_user",
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            setting="api.ws_ticket_max_pending_per_user",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _apply_ws_auth_timeout(app_state: AppState) -> None:
    """Apply the WebSocket auth-timeout seconds from settings.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    try:
        app_state.ws_auth_limits.set_auth_timeout_seconds(
            await config_resolver_of(app_state).get_float(
                SettingNamespace.API.value,
                "ws_auth_timeout_seconds",
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
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

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    ws_limits = app_state.ws_auth_limits

    async def _apply_knob(setting_key: str, setter: Callable[[int], None]) -> None:
        try:
            value = await config_resolver_of(app_state).get_int(
                SettingNamespace.API.value,
                setting_key,
            )
            setter(value)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                setting=setting_key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    # Direct method references (not getattr-by-name) so a setter rename
    # fails type-checking rather than silently breaking at startup.
    await _apply_knob("ws_frame_timeout_seconds", ws_limits.set_frame_timeout_seconds)
    await _apply_knob(
        "auth_revalidate_window_seconds",
        ws_limits.set_auth_revalidate_window_seconds,
    )
    await _apply_knob(
        "auth_revalidate_max_failures",
        ws_limits.set_auth_revalidate_max_failures,
    )


async def _apply_auth_token_bytes(app_state: AppState) -> None:
    """Apply the auth-token entropy width, forcing the default on failure.

    Resolver failures force the cache back to the registered default
    so a prior successful resolve that left the cache at a non-default
    value can't persist past this branch -- the operator-facing log
    must match process state.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    from synthorg.core.auth.token_size import (  # noqa: PLC0415
        _DEFAULT_AUTH_TOKEN_BYTES,
        set_auth_token_bytes,
    )

    try:
        set_auth_token_bytes(
            await config_resolver_of(app_state).get_int(
                SettingNamespace.SECURITY.value,
                "auth_token_bytes",
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
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

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    from synthorg.engine.timeout_enforcement import (  # noqa: PLC0415
        set_timeout_enforcement_enabled,
    )

    try:
        set_timeout_enforcement_enabled(
            value=await config_resolver_of(app_state).get_bool(
                SettingNamespace.ENGINE.value,
                "timeout_enforcement_enabled",
            )
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
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
    integrations = app_state.slice(IntegrationsStateSlice)
    oauth_token_manager = integrations.oauth_token_manager
    if oauth_token_manager is not None:
        oauth_token_manager.set_config_resolver(
            config_resolver_of(app_state),
        )
    communication = app_state.slice(CommunicationStateSlice)
    event_stream_hub = communication.event_stream_hub
    if event_stream_hub is not None:
        event_stream_hub.set_config_resolver(config_resolver_of(app_state))
    bus = communication.message_bus
    if bus is not None:
        set_resolver = getattr(bus, "set_config_resolver", None)
        if callable(set_resolver):
            set_resolver(config_resolver_of(app_state))


async def _apply_audit_chain_signing_timeout(app_state: AppState) -> None:
    """Apply the audit-chain signing timeout to every live sink.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    try:
        signing_timeout = await config_resolver_of(app_state).get_float(
            SettingNamespace.OBSERVABILITY.value,
            "audit_chain_signing_timeout_seconds",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
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
    from synthorg.observability.sinks import (  # noqa: PLC0415
        iter_logging_handlers,
    )

    for handler in iter_logging_handlers():
        if not isinstance(handler, AuditChainSink):
            continue
        try:
            handler.set_signing_timeout_seconds(signing_timeout)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                setting="observability.audit_chain_signing_timeout_seconds",
                phase="apply_to_handler",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


def apply_http_log_handler_settings(snapshot: ObservabilityBridgeConfig) -> None:
    """Push the HTTP log-handler batch knobs onto every live handler.

    Iterates the installed logging handlers and applies the four
    ``observability.http_*`` fields to each :class:`HttpBatchHandler` via its
    setters. Values arrive pre-validated on *snapshot*. A per-handler failure
    is logged and skipped so one bad handler does not abort the others.

    Shared by the startup applier (:func:`_apply_http_log_handler_config`) and
    ``ObservabilityBridgeSettingsSubscriber`` so the boot and hot-reload paths
    apply identically.
    """
    from synthorg.observability.http_handler import (  # noqa: PLC0415
        HttpBatchHandler,
    )
    from synthorg.observability.sinks import (  # noqa: PLC0415
        iter_logging_handlers,
    )

    for handler in iter_logging_handlers():
        if not isinstance(handler, HttpBatchHandler):
            continue
        try:
            handler.set_batch_size(snapshot.http_batch_size)
            handler.set_flush_interval(snapshot.http_flush_interval_seconds)
            handler.set_timeout(snapshot.http_timeout_seconds)
            handler.set_max_retries(snapshot.http_max_retries)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                setting="observability.http_log_handler",
                phase="apply_to_handler",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


async def _apply_http_log_handler_config(app_state: AppState) -> None:
    """Apply the operator-tuned HTTP log-handler batch knobs at startup.

    Resolves the observability bridge snapshot and pushes the four
    ``http_*`` fields onto every live ``HttpBatchHandler`` so a DB override
    applies without a restart (mirrors ``_apply_audit_chain_signing_timeout``).
    By the time this runs ``_apply_bridge_config`` has already swapped the
    observability snapshot onto ``app_state``, so a failed second resolve
    falls back to that already-applied snapshot rather than leaving the live
    handlers on stale boot-time knobs.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    try:
        snapshot = await config_resolver_of(app_state).get_observability_bridge_config()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            setting="observability.http_log_handler",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        snapshot = app_state.bridge_config.observability
    apply_http_log_handler_settings(snapshot)


async def _apply_observability_settings(app_state: AppState) -> None:
    """Apply the DB-resolved console level at startup.

    ``log_level_console`` (at ``configure_logging``) is first resolved from
    the bootstrap chain (env > default) because the DB-backed resolver does
    not exist that early. Once the settings service is wired this step
    re-resolves it through ``ConfigResolver`` (DB > env > default) and
    re-levels the live console handler. A resolver outage leaves the
    bootstrap-applied value untouched. ``telemetry.enabled`` is applied by
    the dedicated ``_apply_telemetry_db_layer`` startup closure, which runs
    adjacent-before the collector ``start`` hook.

    Raises:
        CancelledError: Propagated when the resolver await is cancelled.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return
    resolver = config_resolver_of(app_state)
    try:
        console_level = await resolver.get_str(
            SettingNamespace.OBSERVABILITY.value,
            "log_level_console",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            setting="observability.log_level_console",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    else:
        from synthorg.observability.setup import (  # noqa: PLC0415
            reapply_console_level,
        )

        reapply_console_level(console_level)


async def _apply_bridge_config(
    app_state: AppState,
    effective_config: RootConfig | None,
) -> None:
    """Apply operator-tuned API bridge settings during startup.

    Idempotent via ``app_state.bridge_config.applied`` so a re-entering
    Litestar lifespan (shared-app test fixtures, multi-lifespan runs)
    does not churn httpx/SMTP clients or rebuild the OAuth flow.
    """
    if (
        app_state.slice(SettingsStateSlice).config_resolver is None
        or app_state.bridge_config.applied
    ):
        return

    await _validate_approval_urgency_invariant(app_state)
    await _apply_api_bridge_config_snapshot(app_state)
    await _apply_workers_bridge_config_snapshot(app_state)
    await _apply_memory_bridge_config_snapshot(app_state)
    await _apply_observability_bridge_config_snapshot(app_state)
    await _apply_tools_bridge_config_snapshot(app_state)
    await _apply_ws_ticket_settings(app_state)
    await _apply_ws_auth_timeout(app_state)
    await _apply_ws_dos_settings(app_state)
    await _apply_auth_token_bytes(app_state)
    await _apply_timeout_enforcement(app_state)
    await _apply_sandbox_image_cache(app_state)
    await _apply_fine_tune_image_cache(app_state)
    _wire_resolver_dependents(app_state)
    await _apply_audit_chain_signing_timeout(app_state)
    await _apply_http_log_handler_config(app_state)
    await _apply_observability_settings(app_state)
    await _apply_notification_dispatcher_config(app_state, effective_config)

    app_state.bridge_config.mark_applied()


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

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    if scheduler is None or app_state.slice(SettingsStateSlice).config_resolver is None:
        return
    fallback = registered_default_float(
        SettingNamespace.SECURITY.value,
        "timeout_check_interval_seconds",
    )
    try:
        interval = await config_resolver_of(app_state).get_float(
            SettingNamespace.SECURITY.value,
            "timeout_check_interval_seconds",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
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
