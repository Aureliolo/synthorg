"""Per-component probes behind the readiness / health endpoints.

Each helper answers one component's question and nothing else, so the
controller module holds only the response models, the fan-out and the routes.
The tri-state return (``True`` / ``False`` / ``None``) is the shared contract:
``None`` means the component is unconfigured and therefore does not block a
readiness verdict, which is what keeps a deliberately bus-less or
persistence-less dev stack reporting ready.
"""

from collections.abc import Awaitable, Callable
from enum import StrEnum

from synthorg.api.controllers._memory_health import (
    MemoryHealth,
    resolve_memory_health,
)
from synthorg.api.state import AppState
from synthorg.backup.state import BackupStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_HEALTH_CHECK
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.telemetry.state import TelemetryStateSlice

logger = get_logger(__name__)


class TelemetryStatus(StrEnum):
    """Project-telemetry delivery state as reported to operators."""

    ENABLED = "enabled"
    DISABLED = "disabled"


async def probe_service(
    *,
    configured: bool,
    probe: Callable[[], Awaitable[bool]],
    component: str,
) -> bool | None:
    """Probe an async service, returning None if not configured.

    Returns:
        The ``bool`` value when present, ``None`` otherwise.
    """
    if not configured:
        return None
    try:
        return await probe()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        # ``exc_info=True`` would serialize frame locals from the probe
        # into the log record; persistence / bus probes carry connection
        # objects and partial auth state, so we emit only the sanitized
        # description (see CLAUDE.md ``## Logging``).
        logger.warning(
            API_HEALTH_CHECK,
            component=component,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False


async def probe_persistence(app_state: AppState) -> bool | None:
    """Probe persistence, distinguishing absent-by-design from absent-by-failure.

    A connected backend is health-checked normally. A *missing* backend
    that startup intended to wire (``persistence_expected``) is reported
    UNAVAILABLE (``False``), not ``None``: a configured-but-absent backend
    is a real failure, where treating it as ``None`` would make it
    indistinguishable from a deliberately persistence-less dev run and
    report ready.

    Returns:
        ``True``/``False`` from the health-check, ``False`` when expected
        but absent, or ``None`` when persistence is deliberately
        unconfigured.
    """
    slice_ = app_state.slice(PersistenceStateSlice)
    backend = slice_.backend
    if backend is not None:
        return await probe_service(
            configured=True,
            probe=backend.health_check,
            component="persistence",
        )
    if slice_.persistence_expected:
        logger.warning(
            API_HEALTH_CHECK,
            component="persistence",
            error="persistence expected but no backend is connected",
        )
        return False
    return None


def probe_backup(app_state: AppState) -> bool | None:
    """Report whether the backup service is wired, without probing it.

    Synchronous and outside the probe fan-out because there is nothing to
    call: the question is whether construction succeeded at boot, which the
    slice already records. Mirrors :func:`probe_persistence`'s
    expected-but-absent distinction so a service that failed to build reads
    as ``False`` rather than being indistinguishable from a deliberately
    backup-less run.

    Returns:
        ``True`` when wired, ``False`` when attempted and absent, ``None``
        when backups were never attempted for this boot.
    """
    slice_ = app_state.slice(BackupStateSlice)
    if slice_.service is not None:
        return True
    if slice_.expected:
        logger.warning(
            API_HEALTH_CHECK,
            component="backup",
            error="backup service expected but was never wired",
        )
        return False
    return None


def resolve_telemetry_status(app_state: AppState) -> TelemetryStatus:
    """Read the telemetry collector and map to a public status.

    Returns:
        ``TelemetryStatus`` instance.
    """
    collector = app_state.slice(TelemetryStateSlice).collector
    if collector is None:
        return TelemetryStatus.DISABLED
    return (
        TelemetryStatus.ENABLED if collector.is_functional else TelemetryStatus.DISABLED
    )


async def resolve_memory_state(app_state: AppState) -> MemoryHealth:
    """Report whether agent memory is actually running.

    Thin wrapper binding this module's probe helper to the shared resolver
    in ``_memory_health``.

    Returns:
        ``MemoryHealth`` describing the substrate.
    """
    return await resolve_memory_health(app_state, probe=probe_service)


def memory_readiness(memory_health: MemoryHealth) -> bool | None:
    """Fold agent memory into the readiness verdict.

    Only a wired backend that cannot answer at all (``UNREACHABLE``)
    blocks: its reads and writes are failing, so serving traffic that
    depends on recall would produce errors.

    A DEGRADED backend does not block. Every degradation in that state
    still returns correct results and differs only in latency or in
    matching by term instead of by meaning, so failing readiness for one
    would take a working system offline. It would also collapse the
    distinction the memory design requires be kept: "recall got slower"
    is not "recall changed meaning", and neither is "recall stopped".
    The degradation is reported on the memory surface, which is where an
    operator acts on it.

    An unwired backend (``OFF``) does not block either: the config default
    is ``sqlvector``, so a not-yet-configured deployment reports ``OFF``
    without any durable memory having been wired. ``inmemory`` is reported
    DEGRADED by construction and so likewise never blocks, which is why
    the backend name needs no special case of its own here.

    Returns:
        ``False`` when a wired backend is UNREACHABLE, ``True`` when it is
        DURABLE, or ``None`` (does not block) for a degraded, unwired or
        inmemory store.
    """
    return memory_health.state.readiness
