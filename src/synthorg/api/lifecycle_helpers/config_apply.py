# module-kind: orchestrator
"""Apply operator-tuned bridge config at startup.

Snapshots ``ApiBridgeConfig`` onto ``AppState``, validates cross-
setting invariants (e.g. approval-urgency ordering), populates the
sandbox image-resolution cache, rebuilds the notification dispatcher
with resolved timeouts, and applies the security-timeout scheduler
cadence. Idempotent via ``app_state.bridge_config_applied`` so a
re-entering Litestar lifespan does not churn long-lived clients.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from synthorg.api.api_core_state import ticket_store_of
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.notifications.factory import build_notification_dispatcher
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_BRIDGE_CONFIG_RESOLVE_FAILED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import registered_default_float
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

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

    Raises:
        CancelledError: Raised on the corresponding failure path.
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
            image_value = await config_resolver_of(app_state).get_str(
                SettingNamespace.TOOLS.value, setting_key
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
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
    )
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


async def _apply_bridge_snapshot[T](
    app_state: AppState,
    *,
    bridge: str,
    getter: Callable[[], Awaitable[T]],
    setter: Callable[[T], None],
) -> None:
    """Resolve a bridge-config snapshot once and atomically swap it in.

    Shared body for the ``api`` / ``workers`` / ``memory`` snapshot
    appliers. On any non-fatal resolve failure the default snapshot
    installed by ``AppState.__init__`` is retained and a single
    structured warning is emitted -- the fail-safe rule: a
    settings-backend hiccup must never perturb the live config.

    No-op when no resolver is wired (dev/test rigs that bypass
    ``create_app``); ``getter`` is only invoked after that guard so
    binding it to ``config_resolver_of(app_state)`` stays safe.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return
    try:
        snapshot = await getter()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_BRIDGE_CONFIG_RESOLVE_FAILED,
            bridge=bridge,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback="module_defaults",
        )
        return
    setter(snapshot)


async def _apply_api_bridge_config_snapshot(app_state: AppState) -> None:
    """Snapshot ``ApiBridgeConfig`` onto ``AppState`` at startup.

    Resolves the full bridge once via
    :meth:`ConfigResolver.get_api_bridge_config` and atomically swaps
    it onto ``app_state``. On any non-fatal resolve failure the
    default ``ApiBridgeConfig()`` snapshot installed by
    ``AppState.__init__`` is retained and a single structured warning
    is emitted.

    No-op when no resolver is wired (dev/test rigs that bypass
    ``create_app``); the default snapshot remains in place.
    """
    await _apply_bridge_snapshot(
        app_state,
        bridge="api",
        # Lambda is required: ``config_resolver`` raises until wired, so
        # access must defer past the helper's has_config_resolver guard.
        getter=lambda: config_resolver_of(app_state).get_api_bridge_config(),
        setter=app_state.swap_api_bridge_config,
    )


async def _apply_workers_bridge_config_snapshot(app_state: AppState) -> None:
    """Snapshot ``WorkersBridgeConfig`` onto ``AppState`` at startup.

    Resolves the dispatcher retry budget once via
    :meth:`ConfigResolver.get_workers_bridge_config` and atomically
    swaps it onto ``app_state`` so ``DistributedDispatcher`` observes
    operator-tuned values. On any non-fatal resolve failure the
    default ``WorkersBridgeConfig()`` snapshot (Field defaults ==
    registered ``workers.*`` defaults) is retained -- the fail-safe
    rule: a settings-backend hiccup must not perturb the retry budget.

    No-op when no resolver is wired.
    """
    await _apply_bridge_snapshot(
        app_state,
        bridge="workers",
        # Lambda is required: ``config_resolver`` raises until wired, so
        # access must defer past the helper's has_config_resolver guard.
        getter=lambda: config_resolver_of(app_state).get_workers_bridge_config(),
        setter=app_state.swap_workers_bridge_config,
    )


async def _apply_memory_bridge_config_snapshot(app_state: AppState) -> None:
    """Snapshot ``MemoryBridgeConfig`` onto ``AppState`` at startup.

    Resolves the consolidation enforce-batch + fine-tune preflight
    knobs once via :meth:`ConfigResolver.get_memory_bridge_config` and
    atomically swaps the result onto ``app_state`` so memory consumers
    observe operator-tuned values. On any non-fatal resolve failure the
    default ``MemoryBridgeConfig()`` snapshot (Field defaults ==
    registered ``memory.*`` defaults) is retained -- the fail-safe
    rule: a settings-backend hiccup must not perturb the memory knobs.

    No-op when no resolver is wired.
    """
    await _apply_bridge_snapshot(
        app_state,
        bridge="memory",
        # Lambda is required: ``config_resolver`` raises until wired, so
        # access must defer past the helper's has_config_resolver guard.
        getter=lambda: config_resolver_of(app_state).get_memory_bridge_config(),
        setter=app_state.swap_memory_bridge_config,
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
        app_state.set_ws_auth_timeout_seconds(
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
    for setting_key, setter_name in (
        ("ws_frame_timeout_seconds", "set_ws_frame_timeout_seconds"),
        ("auth_revalidate_window_seconds", "set_auth_revalidate_window_seconds"),
        ("auth_revalidate_max_failures", "set_auth_revalidate_max_failures"),
    ):
        try:
            value = await config_resolver_of(app_state).get_int(
                SettingNamespace.API.value,
                setting_key,
            )
            getattr(app_state, setter_name)(value)
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


async def _apply_auth_token_bytes(app_state: AppState) -> None:
    """Apply the auth-token entropy width, forcing the default on failure.

    Resolver failures force the cache back to the registered default
    so a prior successful resolve that left the cache at a non-default
    value can't persist past this branch -- the operator-facing log
    must match process state.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    from synthorg.api.auth.token_size import (  # noqa: PLC0415
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
    webhook_event_bridge = integrations.webhook_event_bridge
    if webhook_event_bridge is not None:
        webhook_event_bridge.set_config_resolver(
            config_resolver_of(app_state),
        )
    communication = app_state.slice(CommunicationStateSlice)
    escalation_notify_subscriber = communication.escalation_notify_subscriber
    if escalation_notify_subscriber is not None:
        escalation_notify_subscriber.set_config_resolver(
            config_resolver_of(app_state),
        )
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
    from synthorg.observability.startup_wiring import (  # noqa: PLC0415
        _iter_logging_handlers,
    )

    for handler in _iter_logging_handlers():
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


async def _apply_bridge_config(
    app_state: AppState,
    effective_config: RootConfig | None,
) -> None:
    """Apply operator-tuned API bridge settings during startup.

    Idempotent via ``app_state.bridge_config_applied`` so a re-entering
    Litestar lifespan (shared-app test fixtures, multi-lifespan runs)
    does not churn httpx/SMTP clients or rebuild the OAuth flow.
    """
    if (
        app_state.slice(SettingsStateSlice).config_resolver is None
        or app_state.bridge_config_applied
    ):
        return

    await _validate_approval_urgency_invariant(app_state)
    await _apply_api_bridge_config_snapshot(app_state)
    await _apply_workers_bridge_config_snapshot(app_state)
    await _apply_memory_bridge_config_snapshot(app_state)
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
