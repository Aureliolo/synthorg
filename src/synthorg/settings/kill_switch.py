"""Shared resolver-backed kill-switch helper.

Many subsystems advertise a boolean kill switch in the registry that
gates a hot-path call: evolution triggers, training ingestion, meeting
scheduling, evaluation metric recording, request rate limiting, memory
consolidation, escalation sweeping.  Each gate has the same shape --
read the flag once at the entry point, log a single
``KILL_SWITCH_ENGAGED`` audit line when the gate trips, fall back to a
safe default on resolver outage so a transient settings failure cannot
silently flip-flop subsystem behavior.

This helper concentrates that shape so per-subsystem gates stay terse
and consistent across the codebase.
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED

if TYPE_CHECKING:
    # Cycle breaker: hot-path callers across the codebase (including the
    # communication meeting orchestrator, which sits on the resolver's own
    # import chain) import this module at runtime, so the resolver is
    # named for signatures only.
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


async def resolve_bool_with_fallback(
    *,
    resolver: ConfigResolver | None,
    namespace: str,
    key: str,
    fallback: bool,
) -> bool:
    """Resolve a boolean setting through ``ConfigResolver`` with a fallback.

    Returns *fallback* immediately when the resolver is ``None``
    (subsystem not yet wired into AppState, test harness, anonymous
    boot path).  On resolver outage, logs a single
    ``SETTINGS_FETCH_FAILED`` warning and returns *fallback* so a
    transient settings failure cannot collapse a kill switch in either
    direction.

    Args:
        resolver: The application's config resolver, or ``None`` when
            the caller is not yet wired.
        namespace: Setting namespace (e.g. ``"engine"``).
        key: Setting key within the namespace.
        fallback: Value to return when no resolver is wired or the
            lookup fails.  **Callers must pass the same value as the
            registered ``SettingDefinition.default``**: a mismatch
            would cause divergent behaviour between resolver-up and
            resolver-down paths (an operator who sees the documented
            default in the registry would observe a different actual
            value during a settings outage).  This invariant is the
            caller's responsibility -- the helper has no way to look
            up the registered default itself.

    Returns:
        The resolved boolean, or *fallback* on missing resolver / outage.
    """
    if resolver is None:
        return fallback
    try:
        return await resolver.get_bool(namespace, key)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # reraise_critical re-raises MemoryError / RecursionError before any
        # logging or fallback runs. asyncio.CancelledError is a BaseException,
        # so this broad ``except Exception`` never catches it: an aborted await
        # propagates untouched rather than being masked as a settings outage.
        reraise_critical(exc)
        logger.warning(
            SETTINGS_FETCH_FAILED,
            namespace=namespace,
            key=key,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback=fallback,
        )
        return fallback
