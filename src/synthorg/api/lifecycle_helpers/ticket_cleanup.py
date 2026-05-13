"""WS ticket / session / lockout / idempotency cleanup loop.

Periodic cleanup that prunes expired ASGI tickets, sessions, auth
lockouts, OAuth state rows, and idempotency keys. Gated by the
``api.lifecycle_cleanup_enabled`` runtime kill-switch (live, per
tick): when ``False`` every tick short-circuits so operators can
re-enable without restarting the lifespan task.

Also resolves the EventStreamHub janitor settings used by the
``lifecycle_builder`` startup wiring.
"""

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APP_STARTUP,
    API_AUTH_LOCKOUT_CLEANUP,
    API_SESSION_CLEANUP,
    API_WS_TICKET_CLEANUP,
)
from synthorg.observability.events.persistence import (
    PERSISTENCE_OAUTH_STATE_CLEANUP,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import (
    registered_default_bool,
    registered_default_float,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _resolve_ticket_cleanup_interval(app_state: AppState) -> float:
    """Resolve the ticket cleanup interval.

    A settings-backend outage, missing setting, or malformed value must
    not kill the cleanup task -- otherwise expired WS tickets and
    sessions accumulate indefinitely until the next restart. Any
    resolver failure is logged and the registered default for
    ``api.ticket_cleanup_interval_seconds`` is returned, so the fallback
    tracks the registry rather than duplicating the literal here.
    """
    fallback = registered_default_float(
        SettingNamespace.API.value, "ticket_cleanup_interval_seconds"
    )
    if not app_state.has_config_resolver:
        return fallback
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
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_seconds=fallback,
        )
        return fallback


async def _resolve_lifecycle_cleanup_enabled(app_state: AppState) -> bool:
    """Resolve the lifecycle-cleanup kill-switch.

    Operators flip ``api.lifecycle_cleanup_enabled=false`` to pause the
    WS ticket / session / lockout cleanup loop mid-flight without tearing
    down the lifespan task. A settings-backend outage must not mask the
    operator's intent in either direction; the registered default
    ("keep cleaning") wins because stale tickets and sessions accumulate
    forever otherwise.
    """
    fallback = registered_default_bool(
        SettingNamespace.API.value, "lifecycle_cleanup_enabled"
    )
    if not app_state.has_config_resolver:
        return fallback
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
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_enabled=fallback,
        )
        return fallback


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
            failure_context=failure_message,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _resolve_oauth_idempotency_retention(app_state: AppState) -> float:
    """Resolve the OAuth idempotency retention window.

    Falls back to the registered default when the resolver is
    unavailable or the read fails. A settings-backend outage must not
    stop the OAuth state cleanup loop; the table would otherwise grow
    unbounded as consumed-but-stale rows accumulate.
    """
    fallback = registered_default_float(
        SettingNamespace.INTEGRATIONS.value,
        "oauth_idempotency_retention_seconds",
    )
    if not app_state.has_config_resolver:
        return fallback
    try:
        return await app_state.config_resolver.get_float(
            SettingNamespace.INTEGRATIONS.value,
            "oauth_idempotency_retention_seconds",
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            PERSISTENCE_OAUTH_STATE_CLEANUP,
            setting="integrations.oauth_idempotency_retention_seconds",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_seconds=fallback,
        )
        return fallback


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
        # OAuth state cleanup is invoked directly (not via _run_cleanup_step)
        # so we can surface the per-tick removed-row count at the lifecycle
        # observability boundary -- the repo-level log only fires when
        # ``removed > 0`` (silent on no-op), so a lifecycle log gives
        # operators an unconditional "sweep ran" signal.
        oauth_retention_seconds = await _resolve_oauth_idempotency_retention(
            app_state,
        )
        try:
            oauth_removed = await app_state.persistence.oauth_states.cleanup_expired(
                oauth_retention_seconds,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                PERSISTENCE_OAUTH_STATE_CLEANUP,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        else:
            logger.info(
                PERSISTENCE_OAUTH_STATE_CLEANUP,
                note="oauth state sweep completed",
                removed=oauth_removed,
            )
        await _run_cleanup_step(
            app_state.idempotency_service.cleanup_expired,
            event=IDEMPOTENCY_CLEANUP,
            failure_message="Periodic idempotency cleanup failed",
        )


async def _resolve_event_stream_janitor_settings(
    app_state: AppState,
) -> tuple[float, float]:
    """Return ``(idle_ttl, janitor_interval)`` for the EventStreamHub janitor.

    Falls back to the registered defaults when the settings resolver is
    unavailable or either read fails. The fallback keeps the janitor
    enabled rather than disabling pruning on a broken settings backend
    -- leaking subscriber state silently is the worse failure mode.
    """
    fallback_idle = registered_default_float(
        SettingNamespace.COMMUNICATION.value,
        "event_stream_subscriber_idle_ttl_seconds",
    )
    fallback_interval = registered_default_float(
        SettingNamespace.COMMUNICATION.value,
        "event_stream_janitor_interval_seconds",
    )
    if not app_state.has_config_resolver:
        return fallback_idle, fallback_interval
    try:
        idle = await app_state.config_resolver.get_float(
            SettingNamespace.COMMUNICATION.value,
            "event_stream_subscriber_idle_ttl_seconds",
        )
        interval = await app_state.config_resolver.get_float(
            SettingNamespace.COMMUNICATION.value,
            "event_stream_janitor_interval_seconds",
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
            fallback_idle_ttl_seconds=fallback_idle,
            fallback_interval_seconds=fallback_interval,
        )
        return fallback_idle, fallback_interval
    return idle, interval


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
