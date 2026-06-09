"""Webhook-receipt cleanup loop and helpers.

Extracted from :mod:`synthorg.api.lifecycle_helpers` so neither file
exceeds the 800-line size budget.  Wires the per-connection sweep into
the lifecycle alongside :func:`_audit_retention_loop` (24-hour cadence
because receipt retention windows are measured in days, not seconds).

Per-connection retention semantics follow ``Connection.webhook_receipt_retention_days``:

* ``None`` -- use global ``integrations.webhook_receipt_retention_days``
* ``0`` -- never sweep this connection's receipts
* positive integer -- this connection's retention window in days
"""

import asyncio
from typing import TYPE_CHECKING, Final, Literal, NamedTuple

from synthorg.core.clock import SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.persistence.webhook_receipt import (
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP,
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED,
    PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_PAUSED,
)
from synthorg.persistence._shared import paginate
from synthorg.persistence.state import PersistenceStateSlice, persistence_of
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import registered_default_float, registered_default_int
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.integrations.connections.models import Connection
from synthorg.core.clock import Clock


class _CleanupOutcome(NamedTuple):
    """Per-connection cleanup outcome, used to drive the summary log."""

    status: Literal["swept", "skipped", "failed"]
    rows_removed: int  # always 0 unless status == "swept"


logger = get_logger(__name__)

# Page size for the connection sweep; the loop pages until a short
# page so retention guarantees hold regardless of connection count.
_CONNECTION_SWEEP_PAGE_SIZE: Final[int] = 1_000


class _ResolverThrottleState:
    """Per-process throttle flags for webhook-cleanup resolver helpers.

    A mutable singleton lives at module scope so the
    log-once-until-recovery semantics survive across loop ticks (the
    helpers are module-level functions, not class methods, so there is
    no ``self`` to attach the flag to). Wrapping the flags in a class
    rather than declaring two ``bool`` module-level globals keeps
    PLW0603 quiet -- attribute writes on an existing instance do not
    require ``global``. Tests reset state via
    ``_resolver_throttle.cleanup_*_failed = False`` when exercising
    repeated-outage paths.
    """

    cleanup_enabled_failed: bool = False
    cleanup_tick_failed: bool = False


_resolver_throttle = _ResolverThrottleState()


async def _resolve_webhook_receipt_cleanup_enabled(app_state: AppState) -> bool:
    """Resolve the webhook-cleanup kill-switch, fail-safe to ``True``.

    Operators flip ``api.webhook_receipt_cleanup_enabled=false`` to
    pause the per-connection sweep mid-flight without tearing down the
    lifespan task.  A settings-backend outage must not silently flip
    the sweep off (receipts would accumulate forever) -- prefer
    enabled on resolver failure and surface the error.

    Repeated outages are throttled via
    ``_resolver_throttle.cleanup_enabled_failed`` so a prolonged
    settings hiccup logs one warning per failure run instead of one per
    loop tick.

    Returns:
        ``True`` or ``False`` reflecting the condition.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return True
    try:
        value = await config_resolver_of(app_state).get_bool(
            SettingNamespace.API.value, "webhook_receipt_cleanup_enabled"
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        if not _resolver_throttle.cleanup_enabled_failed:
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED,
                reason="kill_switch_resolution_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                fallback_enabled=True,
            )
            _resolver_throttle.cleanup_enabled_failed = True
        return True
    _resolver_throttle.cleanup_enabled_failed = False
    return value


async def _resolve_webhook_receipt_retention(app_state: AppState) -> int:
    """Resolve the global default webhook-receipt retention window (days).

    Falls back to the registered default for
    ``integrations.webhook_receipt_retention_days`` when the settings
    resolver is unavailable or the read fails.  A transient
    settings-backend outage must not silently truncate the receipt log
    nor flip every connection to indefinite retention.

    Logged at ERROR (not WARNING) when the read fails because the
    fallback can override an operator's intentional ``=0`` opt-out: if
    an operator disabled the sweep on purpose, a settings-backend
    hiccup that triggers this fallback will silently re-enable it for
    the remainder of the loop's lifetime.  Surfacing the error makes
    that override discoverable in alerting.

    Returns:
        Resulting integer.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    fallback = registered_default_int(
        SettingNamespace.INTEGRATIONS.value, "webhook_receipt_retention_days"
    )
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return fallback
    try:
        return await config_resolver_of(app_state).get_int(
            SettingNamespace.INTEGRATIONS.value,
            "webhook_receipt_retention_days",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        # ``logger.exception`` would attach a traceback that could leak
        # secret-bearing frame state into structured logs; use
        # ``logger.error`` with ``safe_error_description`` instead.
        log_exception_redacted(
            logger,
            PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED,
            exc,
            message=(
                "Failed to resolve "
                "integrations.webhook_receipt_retention_days; falling back "
                f"to {fallback} days. If an operator set the value to 0 "
                "(disable sweep) it will NOT take effect until the settings "
                "backend recovers."
            ),
            fallback_days=fallback,
        )
        return fallback


async def _cleanup_connection_receipts(
    app_state: AppState,
    conn: Connection,
    default_days: int,
) -> _CleanupOutcome:
    """Sweep one connection's webhook receipts.

    Returns a tagged outcome the caller uses to drive summary logging:

    * ``swept`` -- delete ran; ``rows_removed`` carries the count
    * ``skipped`` -- effective retention <= 0 (per-connection or global opt-out)
    * ``failed`` -- delete raised; the helper logged the warning

    ``MemoryError`` / ``RecursionError`` / ``CancelledError`` propagate
    so the parent loop crashes / cancels rather than masking a fatal
    state.

    Returns:
        ``_CleanupOutcome`` instance.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    override = conn.webhook_receipt_retention_days
    effective = override if override is not None else default_days
    if effective <= 0:
        # Per-connection or global opt-out.
        return _CleanupOutcome("skipped", 0)
    try:
        rows_removed = await persistence_of(
            app_state
        ).webhook_receipts.cleanup_old_for_connection(
            conn.name,
            effective,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED,
            connection_name=str(conn.name),
            retention_days=effective,
            message=(
                "Webhook receipt sweep failed for connection;"
                " continuing with remaining connections"
            ),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _CleanupOutcome("failed", 0)
    return _CleanupOutcome("swept", rows_removed)


def _summarise_sweep(
    *,
    total_removed: int,
    swept: int,
    failed: list[str],
    seen: int,
    default_days: int,
) -> None:
    """Emit the end-of-tick summary log.

    Escalates to ``warning`` when any per-connection sweep failed so
    operators can detect partial sweep failure at a glance instead of
    correlating scattered per-connection warnings.
    """
    log_fn = logger.warning if failed else logger.info
    log_fn(
        PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP,
        note="webhook receipt sweep completed",
        removed=total_removed,
        connections_swept=swept,
        connections_failed=len(failed),
        failed_connection_names=",".join(failed) if failed else None,
        connections_seen=seen,
        default_retention_days=default_days,
    )


async def _webhook_receipt_cleanup_tick(app_state: AppState) -> None:
    """One iteration of the per-connection webhook-receipt sweep.

    Reads the global default retention from
    ``integrations.webhook_receipt_retention_days``, then iterates every
    connection sequentially.  Per-connection failures are logged and
    excluded from the success count; the summary log lists failures by
    name and escalates the log level when any sweep failed.

    Sequential (rather than ``asyncio.TaskGroup``) iteration is
    deliberate: connection lists are small (handful in typical
    deployments), the SQLite write lock would serialise parallel sweeps
    anyway, and sequential keeps the failure-isolation contract simple
    for tests (see ``test_tick_failure_in_one_connection_does_not_abort_others``).

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    default_days = await _resolve_webhook_receipt_retention(app_state)
    try:
        collected: list[Connection] = []
        async for page in paginate(
            lambda limit, offset: persistence_of(app_state).connections.list_items(
                limit=limit, offset=offset
            ),
            page_size=_CONNECTION_SWEEP_PAGE_SIZE,
        ):
            collected.extend(page)
        connections = tuple(collected)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED,
            message="Failed to list connections for webhook receipt sweep",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return

    total_removed = 0
    swept = 0
    failed: list[str] = []
    for conn in connections:
        outcome = await _cleanup_connection_receipts(app_state, conn, default_days)
        if outcome.status == "swept":
            total_removed += outcome.rows_removed
            swept += 1
        elif outcome.status == "failed":
            failed.append(str(conn.name))
        # status == "skipped" => no-op

    _summarise_sweep(
        total_removed=total_removed,
        swept=swept,
        failed=failed,
        seen=len(connections),
        default_days=default_days,
    )


async def _resolve_webhook_receipt_cleanup_tick_seconds(app_state: AppState) -> float:
    """Resolve the cadence between webhook-receipt cleanup ticks.

    Falls back to the registered default
    (``integrations.webhook_receipt_cleanup_tick_seconds``) when the
    resolver is unavailable or the read fails.  Operators tune the
    *window* (per-connection or global
    ``integrations.webhooks.receipt_retention_days``) rather than the
    *cadence*; the cadence remains a setting so a sluggish persistence
    backend can be given a longer interval without code changes.

    Repeated outages are throttled via
    ``_resolver_throttle.cleanup_tick_failed``.

    Returns:
        Resulting numeric value.

    Raises:
        CancelledError: Raised on the corresponding failure path.
    """
    fallback = registered_default_float(
        SettingNamespace.INTEGRATIONS.value,
        "webhook_receipt_cleanup_tick_seconds",
    )
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return fallback
    try:
        value = await config_resolver_of(app_state).get_float(
            SettingNamespace.INTEGRATIONS.value,
            "webhook_receipt_cleanup_tick_seconds",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        reraise_critical(exc)
        if not _resolver_throttle.cleanup_tick_failed:
            logger.warning(
                PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_FAILED,
                reason="tick_seconds_resolution_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                fallback_seconds=fallback,
            )
            _resolver_throttle.cleanup_tick_failed = True
        return fallback
    _resolver_throttle.cleanup_tick_failed = False
    return value


async def _webhook_receipt_cleanup_loop(
    app_state: AppState,
    *,
    clock: Clock | None = None,
) -> None:
    """Daily sweep that prunes webhook receipts per connection.

    Mirrors :func:`synthorg.api.lifecycle_helpers._audit_retention_loop`:
    a separate daily loop kept out of the 60-second cleanup tick
    (sessions / lockouts / OAuth states / idempotency keys) because
    receipt retention is durable (days, weeks, months) rather than
    transient (seconds, minutes).

    Gated by ``api.webhook_receipt_cleanup_enabled`` (live, per-tick):
    when the setting is ``False`` every 24h tick short-circuits -- the
    loop keeps running so operators can re-enable without restarting,
    but no sweep work is done.

    ``clock`` is the time-injection seam: production wires
    :class:`SystemClock` (which delegates to ``asyncio.sleep``) and
    tests inject ``FakeClock`` so the loop is driven deterministically
    via ``clock.advance_async()`` rather than via ``asyncio.sleep``
    monkey-patching.
    """
    effective_clock: Clock = clock if clock is not None else SystemClock()
    while True:
        if await _resolve_webhook_receipt_cleanup_enabled(app_state):
            await _webhook_receipt_cleanup_tick(app_state)
        else:
            logger.debug(
                PERSISTENCE_WEBHOOK_RECEIPT_CLEANUP_PAUSED,
                reason="paused_by_setting",
            )
        await effective_clock.sleep(
            await _resolve_webhook_receipt_cleanup_tick_seconds(app_state)
        )
