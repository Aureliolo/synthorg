"""Smaller lifecycle helpers used by :mod:`synthorg.api.lifecycle_builder`.

Split out of ``lifecycle_builder.py`` so neither file exceeds the
800-line size budget. Consumers should import from
``lifecycle_builder`` which re-exports these names.
"""

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from synthorg.notifications.factory import build_notification_dispatcher
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_AUDIT_RETENTION,
    API_AUTH_LOCKOUT_CLEANUP,
    API_SESSION_CLEANUP,
    API_WS_TICKET_CLEANUP,
)
from synthorg.observability.events.setup import SETUP_AGENT_BOOTSTRAP_FAILED
from synthorg.settings.dispatcher import SettingsChangeDispatcher
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.subscribers import (
    BackupSettingsSubscriber,
    MemorySettingsSubscriber,
    ObservabilitySettingsSubscriber,
    PerOpRateLimitSettingsSubscriber,
    ProviderSettingsSubscriber,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from synthorg.api.state import AppState
    from synthorg.backup.service import BackupService
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.config.schema import RootConfig
    from synthorg.settings.service import SettingsService
    from synthorg.settings.subscriber import SettingsSubscriber

logger = get_logger(__name__)


async def _resolve_ticket_cleanup_interval(app_state: AppState) -> float:
    """Resolve the ticket cleanup interval, falling back to 60 seconds.

    A settings-backend outage, missing setting, or malformed value must
    not kill the cleanup task -- otherwise expired WS tickets and
    sessions accumulate indefinitely until the next restart. Any
    resolver failure is logged and the built-in default is returned.
    """
    if not app_state.has_config_resolver:
        return 60.0
    try:
        return await app_state.config_resolver.get_float(
            SettingNamespace.API.value, "ticket_cleanup_interval_seconds"
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_WS_TICKET_CLEANUP,
            error=(
                "Failed to resolve ticket_cleanup_interval_seconds;"
                " falling back to 60.0 seconds"
            ),
            error_type=type(exc).__name__,
            error_desc=safe_error_description(exc),
        )
        return 60.0


async def _resolve_lifecycle_cleanup_enabled(app_state: AppState) -> bool:
    """Resolve the lifecycle-cleanup kill-switch, fail-safe to ``True``.

    Operators flip ``api.lifecycle_cleanup_enabled=false`` to pause the
    WS ticket / session / lockout cleanup loop mid-flight without tearing
    down the lifespan task.  A settings-backend outage must not mask the
    operator's intent in either direction -- we pick "keep cleaning" as
    the safer failure mode because stale tickets and sessions accumulate
    forever otherwise.
    """
    if not app_state.has_config_resolver:
        return True
    try:
        return await app_state.config_resolver.get_bool(
            SettingNamespace.API.value, "lifecycle_cleanup_enabled"
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_WS_TICKET_CLEANUP,
            error=(
                "Failed to resolve lifecycle_cleanup_enabled; defaulting to enabled"
            ),
            error_type=type(exc).__name__,
            error_desc=safe_error_description(exc),
        )
        return True


async def _run_cleanup_step(
    action: Callable[[], Awaitable[Any] | Any],
    *,
    event: str,
    failure_message: str,
) -> None:
    """Run *action* and log any non-system exception under *event*.

    ``action`` may return either an awaitable (async cleanup) or a
    synchronous result (e.g. ``ticket_store.cleanup_expired()``). The
    caller decides whether to gate the call on a ``has_*`` predicate
    before invoking the helper, keeping the helper's surface
    deliberately narrow.

    ``MemoryError`` / ``RecursionError`` propagate so the parent loop
    can crash on truly fatal failures rather than masking them as a
    routine cleanup blip.
    """
    try:
        result = action()
        # Use ``inspect.isawaitable`` so Tasks, Futures, and any
        # ``__await__``-implementing custom awaitable returned by a
        # cleanup hook are awaited too -- ``asyncio.iscoroutine``
        # only matches bare coroutines and would silently skip the
        # rest.
        if inspect.isawaitable(result):
            await result
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            event,
            error=failure_message,
            error_type=type(exc).__name__,
            error_desc=safe_error_description(exc),
        )


async def _run_cleanup_tick(app_state: AppState) -> None:
    """Run one cleanup-cycle tick across every store.

    Covers WS tickets, sessions, lockouts, and idempotency keys. Each
    store's cleanup is delegated to :func:`_run_cleanup_step` so a
    transient failure in one store cannot abort the others.
    """
    from synthorg.observability.events.idempotency import (  # noqa: PLC0415
        IDEMPOTENCY_CLEANUP,
    )

    await _run_cleanup_step(
        app_state.ticket_store.cleanup_expired,
        event=API_WS_TICKET_CLEANUP,
        failure_message="Periodic ticket cleanup failed",
    )
    if app_state.has_session_store:
        await _run_cleanup_step(
            app_state.session_store.cleanup_expired,
            event=API_SESSION_CLEANUP,
            failure_message="Periodic session cleanup failed",
        )
    if app_state.has_lockout_store:
        await _run_cleanup_step(
            app_state.lockout_store.cleanup_expired,
            event=API_AUTH_LOCKOUT_CLEANUP,
            failure_message="Periodic lockout cleanup failed",
        )
    if app_state.has_persistence:
        await _run_cleanup_step(
            app_state.idempotency_service.cleanup_expired,
            event=IDEMPOTENCY_CLEANUP,
            failure_message="Periodic idempotency cleanup failed",
        )


async def _ticket_cleanup_loop(app_state: AppState) -> None:
    """Periodically prune expired WS tickets and sessions.

    Gated by ``api.lifecycle_cleanup_enabled`` (live, per-tick): when
    the setting is ``False`` every tick short-circuits -- the loop
    keeps running so operators can re-enable without restarting, but
    no cleanup work is done.
    """
    while True:
        await asyncio.sleep(await _resolve_ticket_cleanup_interval(app_state))
        if not await _resolve_lifecycle_cleanup_enabled(app_state):
            logger.debug(API_WS_TICKET_CLEANUP, reason="paused_by_setting")
            continue
        await _run_cleanup_tick(app_state)


_DEFAULT_AUDIT_RETENTION_DAYS = 730


async def _resolve_audit_retention(
    app_state: AppState,
) -> tuple[int, bool]:
    """Resolve ``(retention_days, paused)`` for the audit retention loop.

    Falls back to the registered default (``730`` days, unpaused) when
    the settings resolver is unavailable or either read fails.  The
    fallback intentionally keeps retention enabled rather than
    disabling purging on a broken settings backend -- leaving expired
    audit rows around is a compliance risk, so prefer the built-in
    default to a silent zero.  ``0`` is reserved for an operator
    explicitly opting out via ``security.audit_retention_days=0``.
    """
    if not app_state.has_config_resolver:
        return _DEFAULT_AUDIT_RETENTION_DAYS, False
    try:
        days = await app_state.config_resolver.get_int(
            SettingNamespace.SECURITY.value, "audit_retention_days"
        )
        paused_raw = await app_state.config_resolver.get_bool(
            SettingNamespace.SECURITY.value, "retention_cleanup_paused"
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            error=(
                "Failed to resolve audit retention settings;"
                " falling back to default retention window"
            ),
            error_type=type(exc).__name__,
            error_desc=safe_error_description(exc),
            fallback_days=_DEFAULT_AUDIT_RETENTION_DAYS,
        )
        return _DEFAULT_AUDIT_RETENTION_DAYS, False
    return days, paused_raw


async def _audit_retention_tick(app_state: AppState) -> None:
    """Single iteration of the audit retention sweep.

    Extracted from ``_audit_retention_loop`` so the loop body stays
    under the project function-length limit.
    """
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    days, paused = await _resolve_audit_retention(app_state)
    if paused:
        logger.info(API_AUDIT_RETENTION, note="audit retention purge paused")
        return
    if days <= 0:
        logger.debug(API_AUDIT_RETENTION, note="audit retention purge disabled")
        return
    if not app_state.has_persistence:
        return
    cutoff = datetime.now(UTC) - timedelta(days=days)
    try:
        deleted = await app_state.persistence.audit_entries.purge_before(cutoff)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_AUDIT_RETENTION,
            note="audit retention purge failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(
        API_AUDIT_RETENTION,
        note="audit retention purge completed",
        deleted=deleted,
        retention_days=days,
        cutoff=cutoff.isoformat(),
    )


async def _audit_retention_loop(app_state: AppState) -> None:
    """Daily sweep that purges audit_entries older than retention window.

    Reads ``security.audit_retention_days`` and
    ``security.retention_cleanup_paused`` from the settings resolver
    on every tick so operator changes take effect without restart.
    A ``retention_days`` of 0 disables purging entirely (opt-out via
    ``security.audit_retention_days=0``); resolver outages fall back
    to the registered default of 730 days rather than disabling
    retention. The loop stays resident even when paused so lifecycle
    plumbing is unchanged. Tick cadence is 24h -- audit retention is
    not a hot path.
    """
    tick_seconds = 86_400.0
    while True:
        await _audit_retention_tick(app_state)
        await asyncio.sleep(tick_seconds)


async def _maybe_promote_first_owner(app_state: AppState) -> None:
    """Promote the first user to owner if no owner exists.

    This is a one-time idempotent migration that runs on every boot
    until at least one user has the ``OrgRole.OWNER`` role.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    if not app_state.has_persistence:
        return
    try:
        users = await app_state.persistence.users.list_users()
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            note="Owner auto-promote skipped: failed to list users",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    if not users:
        return

    from synthorg.api.auth.models import OrgRole  # noqa: PLC0415

    has_owner = any(OrgRole.OWNER in u.org_roles for u in users)
    if has_owner:
        return

    first = users[0]
    promoted = first.model_copy(
        update={
            "org_roles": (*first.org_roles, OrgRole.OWNER),
            "updated_at": datetime.now(UTC),
        },
    )
    try:
        await app_state.persistence.users.save(promoted)
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            note="Owner auto-promote failed",
            user_id=first.id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(
        API_APP_STARTUP,
        note="Auto-promoted first user to owner",
        user_id=first.id,
        username=first.username,
    )


async def _maybe_bootstrap_agents(app_state: AppState) -> None:
    """Bootstrap agents if setup is complete and services are available.

    On first run, setup isn't complete yet so bootstrap is deferred
    to ``POST /setup/complete``.  On subsequent starts, agents are
    loaded from persisted config into the runtime registry.
    """
    if not (
        app_state.has_config_resolver
        and app_state.has_agent_registry
        and app_state.has_settings_service
    ):
        logger.debug(
            API_APP_STARTUP,
            note="Agent bootstrap skipped: required services not available",
        )
        return

    try:
        setup_entry = await app_state.settings_service.get_entry(
            "api",
            "setup_complete",
        )
        is_complete = setup_entry.value == "true"
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            note="Could not read setup_complete setting; skipping agent bootstrap",
            namespace="api",
            key="setup_complete",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        is_complete = False

    if not is_complete:
        logger.debug(
            API_APP_STARTUP,
            note="Agent bootstrap skipped: setup not complete",
        )
        return

    try:
        from synthorg.api.bootstrap import bootstrap_agents  # noqa: PLC0415

        await bootstrap_agents(
            config_resolver=app_state.config_resolver,
            agent_registry=app_state.agent_registry,
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            SETUP_AGENT_BOOTSTRAP_FAILED,
            error="Agent bootstrap failed at startup (non-fatal)",
            error_type=type(exc).__name__,
            error_desc=safe_error_description(exc),
        )


def _build_settings_dispatcher(
    message_bus: MessageBus | None,
    settings_service: SettingsService | None,
    config: RootConfig,
    app_state: AppState,
    backup_service: BackupService | None = None,
) -> SettingsChangeDispatcher | None:
    """Create settings change dispatcher if bus and settings are available."""
    if message_bus is None or settings_service is None:
        return None
    provider_sub = ProviderSettingsSubscriber(
        config=config,
        app_state=app_state,
        settings_service=settings_service,
    )
    memory_sub = MemorySettingsSubscriber()
    log_dir = config.logging.log_dir if config.logging is not None else "logs"
    observability_sub = ObservabilitySettingsSubscriber(
        settings_service=settings_service,
        log_dir=log_dir,
    )
    per_op_rl_sub = PerOpRateLimitSettingsSubscriber(
        app_state=app_state,
        settings_service=settings_service,
    )
    subs: list[SettingsSubscriber] = [
        provider_sub,
        memory_sub,
        observability_sub,
        per_op_rl_sub,
    ]
    if backup_service is not None:
        subs.append(
            BackupSettingsSubscriber(
                backup_service=backup_service,
                settings_service=settings_service,
            ),
        )
    return SettingsChangeDispatcher(
        message_bus=message_bus,
        subscribers=tuple(subs),
    )


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
            error=(
                "Failed to resolve approval-urgency settings for"
                " invariant check; skipping"
            ),
            error_type=type(exc).__name__,
            error_desc=safe_error_description(exc),
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


async def _apply_bridge_config(  # noqa: C901, PLR0912, PLR0915
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

    try:
        app_state.ticket_store.set_max_pending_per_user(
            await app_state.config_resolver.get_int(
                SettingNamespace.API.value,
                "ws_ticket_max_pending_per_user",
            )
        )
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_APP_STARTUP,
            error=(
                "Failed to apply ws_ticket_max_pending_per_user; using built-in default"
            ),
            exc_info=True,
        )

    try:
        app_state.set_ws_auth_timeout_seconds(
            await app_state.config_resolver.get_float(
                SettingNamespace.API.value,
                "ws_auth_timeout_seconds",
            )
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            error=("Failed to apply ws_auth_timeout_seconds; using built-in default"),
            error_type=type(exc).__name__,
            error_desc=safe_error_description(exc),
        )

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
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            error=("Failed to apply security.auth_token_bytes; using built-in default"),
            error_type=type(exc).__name__,
            error_desc=safe_error_description(exc),
            fallback_bytes=_DEFAULT_AUTH_TOKEN_BYTES,
        )

    try:
        from synthorg.engine.timeout_enforcement import (  # noqa: PLC0415
            set_timeout_enforcement_enabled,
        )

        set_timeout_enforcement_enabled(
            value=await app_state.config_resolver.get_bool(
                SettingNamespace.ENGINE.value,
                "timeout_enforcement_enabled",
            )
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_APP_STARTUP,
            error=(
                "Failed to apply engine.timeout_enforcement_enabled;"
                " keeping enforcement on (safe default)"
            ),
            error_type=type(exc).__name__,
            error_desc=safe_error_description(exc),
            fallback_enabled=True,
        )

    if app_state.oauth_token_manager is not None:
        app_state.oauth_token_manager.set_config_resolver(
            app_state.config_resolver,
        )
    if app_state.webhook_event_bridge is not None:
        app_state.webhook_event_bridge.set_config_resolver(
            app_state.config_resolver,
        )
    _bus = app_state.message_bus if app_state.has_message_bus else None
    if _bus is not None:
        _set_resolver = getattr(_bus, "set_config_resolver", None)
        if callable(_set_resolver):
            _set_resolver(app_state.config_resolver)

    try:
        signing_timeout = await app_state.config_resolver.get_float(
            SettingNamespace.OBSERVABILITY.value,
            "audit_chain_signing_timeout_seconds",
        )
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_APP_STARTUP,
            error=(
                "Failed to resolve audit_chain_signing_timeout_seconds;"
                " keeping sink default"
            ),
            exc_info=True,
        )
    else:
        from synthorg.observability.audit_chain.sink import (  # noqa: PLC0415
            AuditChainSink,
        )
        from synthorg.observability.startup_wiring import (  # noqa: PLC0415
            _iter_logging_handlers,
        )

        for _handler in _iter_logging_handlers():
            if isinstance(_handler, AuditChainSink):
                try:
                    _handler.set_signing_timeout_seconds(signing_timeout)
                except MemoryError, RecursionError:
                    raise
                except Exception:
                    logger.warning(
                        API_APP_STARTUP,
                        error=(
                            "Failed to apply"
                            " audit_chain_signing_timeout_seconds"
                            " to handler"
                        ),
                        exc_info=True,
                    )

    try:
        notif_bridge = await app_state.config_resolver.get_notifications_bridge_config()
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_APP_STARTUP,
            error=(
                "Failed to resolve notifications bridge config;"
                " keeping dispatcher default timeouts"
            ),
            exc_info=True,
        )
    else:
        if app_state.has_notification_dispatcher and effective_config is not None:
            _new_dispatcher = build_notification_dispatcher(
                effective_config.notifications,
                bridge_config=notif_bridge,
            )
            _old_dispatcher = app_state.swap_notification_dispatcher(_new_dispatcher)
            if _old_dispatcher is not None:
                try:
                    await _old_dispatcher.aclose()
                except MemoryError, RecursionError:
                    raise
                except Exception:
                    logger.warning(
                        API_APP_STARTUP,
                        error=(
                            "Failed to close pre-startup notification"
                            " dispatcher sinks after rebuild"
                        ),
                        exc_info=True,
                    )

    app_state.mark_bridge_config_applied()
