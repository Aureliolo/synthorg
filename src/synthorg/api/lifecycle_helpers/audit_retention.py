"""Daily audit-table retention sweep.

Purges ``audit_entries`` rows older than ``security.audit_retention_days``.
``0`` opts out of purging; ``security.audit_retention_loop_enabled``
is a runtime kill-switch that leaves the loop resident but inert when
``False``. Resolver outages fall back to the registered defaults
rather than disabling retention -- leaving expired audit rows around
is a compliance risk.
"""

import asyncio
import math
from typing import TYPE_CHECKING

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_AUDIT_RETENTION
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import (
    registered_default_bool,
    registered_default_float,
    registered_default_int,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


async def _resolve_audit_retention_loop_enabled(app_state: AppState) -> bool:
    """Return whether the audit retention loop is enabled (live, per-tick).

    Reads ``security.audit_retention_loop_enabled`` from the settings
    resolver. Falls back to the registered default (``True``) when the
    resolver is unavailable or the read fails -- leaving expired audit
    rows around is a compliance risk, so the loop stays active on a
    broken settings backend rather than silently disabling itself.
    """
    fallback = registered_default_bool(
        SettingNamespace.SECURITY.value, "audit_retention_loop_enabled"
    )
    if not app_state.has_config_resolver:
        return fallback
    try:
        return await app_state.config_resolver.get_bool(
            SettingNamespace.SECURITY.value, "audit_retention_loop_enabled"
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_AUDIT_RETENTION,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_enabled=fallback,
        )
        return fallback


async def _resolve_audit_retention_days(app_state: AppState) -> int:
    """Resolve ``audit_retention_days`` for the retention loop.

    Falls back to the registered default when the settings resolver is
    unavailable or the read fails. The fallback intentionally keeps
    retention enabled rather than disabling purging on a broken
    settings backend -- leaving expired audit rows around is a
    compliance risk, so prefer the built-in default to a silent zero.
    ``0`` is reserved for an operator explicitly opting out via
    ``security.audit_retention_days=0``.
    """
    fallback = registered_default_int(
        SettingNamespace.SECURITY.value, "audit_retention_days"
    )
    if not app_state.has_config_resolver:
        return fallback
    try:
        return await app_state.config_resolver.get_int(
            SettingNamespace.SECURITY.value, "audit_retention_days"
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_AUDIT_RETENTION,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_days=fallback,
        )
        return fallback


async def _resolve_audit_retention_tick_seconds(app_state: AppState) -> float:
    """Resolve the cadence between audit retention purge ticks.

    Falls back to the registered default when the resolver is
    unavailable, the read fails, or the resolved value is
    non-finite / non-positive. Skipping the validation would let a
    tampered setting feed ``asyncio.sleep(-1)`` (tight loop) or
    ``asyncio.sleep(nan)`` (loop crash) into the purge worker.
    """
    fallback = registered_default_float(
        SettingNamespace.SECURITY.value, "audit_retention_tick_seconds"
    )
    if not app_state.has_config_resolver:
        return fallback
    try:
        seconds = await app_state.config_resolver.get_float(
            SettingNamespace.SECURITY.value, "audit_retention_tick_seconds"
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            API_AUDIT_RETENTION,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_seconds=fallback,
        )
        return fallback
    if not math.isfinite(seconds) or seconds <= 0:
        logger.warning(
            API_AUDIT_RETENTION,
            note="invalid audit retention tick seconds; using fallback",
            resolved_seconds=seconds,
            fallback_seconds=fallback,
        )
        return fallback
    return seconds


async def _audit_retention_tick(app_state: AppState) -> None:
    """Single iteration of the audit retention sweep.

    Extracted from ``_audit_retention_loop`` so the loop body stays
    under the project function-length limit.
    """
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    days = await _resolve_audit_retention_days(app_state)
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
    ``security.audit_retention_loop_enabled`` from the settings
    resolver on every tick so operator changes take effect without
    restart. A ``retention_days`` of 0 disables purging entirely
    (opt-out via ``security.audit_retention_days=0``); the kill-switch
    keeps the loop resident but inert so plumbing is unchanged when
    operators pause retention during incident investigation.
    Resolver outages fall back to the registered defaults rather than
    disabling retention -- leaving expired audit rows around is a
    compliance risk. Tick cadence comes from
    ``security.audit_retention_tick_seconds`` (default 24h).
    """
    while True:
        if await _resolve_audit_retention_loop_enabled(app_state):
            await _audit_retention_tick(app_state)
        else:
            logger.info(
                API_AUDIT_RETENTION,
                note="audit retention purge paused",
                reason="paused_by_setting",
            )
        await asyncio.sleep(await _resolve_audit_retention_tick_seconds(app_state))
