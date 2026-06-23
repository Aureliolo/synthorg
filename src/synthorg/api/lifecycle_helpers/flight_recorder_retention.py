"""Daily flight-recorder retention sweep.

Purges ``flight_recorder_frames`` rows older than
``cockpit.flight_recorder_retention_days``.
``cockpit.flight_recorder_retention_loop_enabled`` is a runtime
kill-switch that leaves the loop resident but inert when ``False``.
Resolver outages fall back to the registered defaults rather than
disabling retention, so an unbounded recorder table does not silently
accumulate during a settings-backend outage.
"""

import asyncio
from datetime import timedelta
from typing import Final

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_FLIGHT_RECORDER_RETENTION
from synthorg.persistence.state import PersistenceStateSlice, persistence_of
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import (
    registered_default_bool,
    registered_default_int,
)
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)

# Daily cadence between purge ticks. A protocol/algorithm constant, not
# an operator policy knob: the retention window itself is tunable via
# ``cockpit.flight_recorder_retention_days``.
_TICK_SECONDS: Final[float] = 86400.0


async def _resolve_loop_enabled(app_state: AppState) -> bool:
    """Return whether the retention loop is enabled (live, per-tick).

    Falls back to the registered default (``True``) when the resolver is
    unavailable or the read fails, so an unbounded recorder table does
    not silently accumulate on a broken settings backend.

    Returns:
        ``True`` or ``False`` reflecting the resolved kill-switch.

    Raises:
        CancelledError: Propagated so the loop's cancellation is clean.
    """
    fallback = registered_default_bool(
        SettingNamespace.COCKPIT.value, "flight_recorder_retention_loop_enabled"
    )
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        logger.debug(
            API_FLIGHT_RECORDER_RETENTION,
            note="settings resolver unavailable; using registered default",
            fallback_enabled=fallback,
        )
        return fallback
    try:
        return await config_resolver_of(app_state).get_bool(
            SettingNamespace.COCKPIT.value, "flight_recorder_retention_loop_enabled"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_FLIGHT_RECORDER_RETENTION,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_enabled=fallback,
        )
        return fallback


async def _resolve_retention_days(app_state: AppState) -> int:
    """Resolve ``flight_recorder_retention_days`` for the retention loop.

    Falls back to the registered default when the resolver is
    unavailable, the read fails, or the resolved value is negative. A
    negative resolver value is invalid (the setting's ``min_value`` is
    1), so it reverts to the fallback rather than being treated as an
    opt-out.

    Returns:
        The resolved retention window in days.

    Raises:
        CancelledError: Propagated so the loop's cancellation is clean.
    """
    fallback = registered_default_int(
        SettingNamespace.COCKPIT.value, "flight_recorder_retention_days"
    )
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        logger.debug(
            API_FLIGHT_RECORDER_RETENTION,
            note="settings resolver unavailable; using registered default",
            fallback_days=fallback,
        )
        return fallback
    try:
        days = await config_resolver_of(app_state).get_int(
            SettingNamespace.COCKPIT.value, "flight_recorder_retention_days"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_FLIGHT_RECORDER_RETENTION,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_days=fallback,
        )
        return fallback
    if days < 1:
        logger.warning(
            API_FLIGHT_RECORDER_RETENTION,
            note="invalid flight-recorder retention days; using fallback",
            resolved_days=days,
            fallback_days=fallback,
        )
        return fallback
    return days


async def _retention_tick(app_state: AppState) -> None:
    """Single iteration of the flight-recorder retention sweep."""
    days = await _resolve_retention_days(app_state)
    if app_state.slice(PersistenceStateSlice).backend is None:
        logger.debug(
            API_FLIGHT_RECORDER_RETENTION,
            note="flight-recorder retention skipped; no persistence backend",
        )
        return
    cutoff = app_state.clock.now() - timedelta(days=days)
    try:
        deleted = await persistence_of(app_state).flight_recorder_frames.purge_before(
            cutoff
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_FLIGHT_RECORDER_RETENTION,
            note="flight-recorder retention purge failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(
        API_FLIGHT_RECORDER_RETENTION,
        note="flight-recorder retention purge completed",
        deleted=deleted,
        retention_days=days,
        cutoff=cutoff.isoformat(),
    )


async def _flight_recorder_retention_loop(app_state: AppState) -> None:
    """Daily sweep purging flight-recorder frames past the retention window.

    Reads ``cockpit.flight_recorder_retention_days`` and
    ``cockpit.flight_recorder_retention_loop_enabled`` from the settings
    resolver on every tick so operator changes take effect without a
    restart. The kill-switch keeps the loop resident but inert when an
    operator pauses retention; resolver outages fall back to the
    registered defaults rather than disabling retention.
    """
    while True:
        if await _resolve_loop_enabled(app_state):
            await _retention_tick(app_state)
        else:
            logger.info(
                API_FLIGHT_RECORDER_RETENTION,
                note="flight-recorder retention purge paused",
                reason="paused_by_setting",
            )
        await asyncio.sleep(_TICK_SECONDS)
