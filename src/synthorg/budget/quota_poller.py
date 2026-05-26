"""Proactive quota polling and alerting service.

Runs a background ``asyncio.Task`` that periodically polls quota
snapshots and dispatches notifications when usage thresholds are
crossed.  Cooldown tracking prevents alert storms for the same
provider/window/level combination.

Individual poll failures are logged and do not propagate.
"""

import asyncio
import contextlib
from typing import TYPE_CHECKING

from synthorg.budget.quota import QuotaSnapshot, QuotaWindow
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.quota import (
    QUOTA_ALERT_COOLDOWN_ACTIVE,
    QUOTA_POLL_COMPLETED,
    QUOTA_POLL_FAILED,
    QUOTA_POLL_STARTED,
    QUOTA_POLLER_STARTED,
    QUOTA_POLLER_STOPPED,
    QUOTA_THRESHOLD_ALERT,
)

if TYPE_CHECKING:
    from synthorg.budget.quota_poller_config import QuotaPollerConfig
    from synthorg.budget.quota_tracker import QuotaTracker
    from synthorg.notifications.dispatcher import NotificationDispatcher

logger = get_logger(__name__)

# Cooldown key: (provider_name, window, severity_level)
_CooldownKey = tuple[str, QuotaWindow, str]


class QuotaPoller:
    """Polls quota snapshots and dispatches threshold alerts.

    Args:
        quota_tracker: Source of quota snapshots.
        config: Poller configuration.
        notification_dispatcher: Optional dispatcher for alerts.
    """

    def __init__(
        self,
        *,
        quota_tracker: QuotaTracker,
        config: QuotaPollerConfig,
        notification_dispatcher: NotificationDispatcher | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._tracker = quota_tracker
        self._config = config
        self._dispatcher = notification_dispatcher
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._task: asyncio.Task[None] | None = None
        # Held across the full body of ``start`` and ``stop`` so the
        # check-and-mutate of ``self._task`` is atomic against
        # concurrent callers, per CLAUDE.md "Lifecycle synchronization".
        # Eager init: stop() must be safe to call before start() has
        # ever run, so a half-published lock attribute would race.
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        self._cooldown: dict[_CooldownKey, float] = {}

    async def start(self) -> None:
        """Start the background polling loop.

        Creates an ``asyncio.Task`` that calls :meth:`poll_once`
        repeatedly at the configured interval.  Calling ``start()``
        when already running is a no-op.
        """
        async with self._lifecycle_lock:
            if self._task is not None and not self._task.done():
                return
            if self._task is not None and self._task.done():
                self._task = None
            self._task = asyncio.get_running_loop().create_task(
                self._poll_loop(),
                name="quota-poller",
            )
            logger.info(
                QUOTA_POLLER_STARTED,
                interval=self._config.poll_interval_seconds,
            )

    async def stop(self) -> None:
        """Cancel the background polling task and wait for it to finish."""
        async with self._lifecycle_lock:
            if self._task is None:
                return
            if self._task.done():
                # The task already exited (e.g. cancellation racing
                # with a self-finishing loop body). Clear the
                # reference so a subsequent ``start()`` is treated as
                # a fresh cold start instead of seeing a stale done
                # task and short-circuiting.
                self._task = None
                return
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            logger.info(QUOTA_POLLER_STOPPED)

    async def poll_once(self) -> None:
        """Execute a single poll cycle.

        Fetches snapshots for all tracked providers, computes usage
        percentages, and dispatches alerts for any threshold crossings
        not in cooldown.  Errors from the tracker are logged and do not
        propagate.
        """
        logger.debug(QUOTA_POLL_STARTED)
        try:
            snapshots = await self._tracker.get_all_snapshots()
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(logger, QUOTA_POLL_FAILED, exc)
            return

        for provider_snapshots in snapshots.values():
            for snap in provider_snapshots:
                await self._check_snapshot(snap)

        logger.debug(QUOTA_POLL_COMPLETED)

    # ── Private helpers ──────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Background task: poll repeatedly until cancelled.

        Raises:
            CancelledError: If the related operation fails.
        """
        # lint-allow: long-running-loop-kill-switch -- stop()/cancel drives shutdown.
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reraise_critical(exc)
                log_exception_redacted(logger, QUOTA_POLL_FAILED, exc)
            await self._clock.sleep(self._config.poll_interval_seconds)

    async def _check_snapshot(self, snap: QuotaSnapshot) -> None:
        """Evaluate a single snapshot and dispatch an alert if needed."""
        usage_pct = _compute_usage_pct(snap)
        if usage_pct is None:
            return

        thresholds = self._config.alert_thresholds
        if usage_pct >= thresholds.critical_pct:
            level = "critical"
        elif usage_pct >= thresholds.warn_pct:
            level = "warning"
        else:
            return

        key: _CooldownKey = (snap.provider_name, snap.window, level)
        now = self._clock.monotonic()
        last = self._cooldown.get(key)
        if last is not None:
            elapsed = now - last
            if elapsed < self._config.cooldown_seconds:
                logger.debug(
                    QUOTA_ALERT_COOLDOWN_ACTIVE,
                    provider=snap.provider_name,
                    window=snap.window.value,
                    level=level,
                    remaining=max(0.0, self._config.cooldown_seconds - elapsed),
                )
                return

        logger.warning(
            QUOTA_THRESHOLD_ALERT,
            provider=snap.provider_name,
            window=snap.window.value,
            level=level,
            usage_pct=usage_pct,
        )
        self._cooldown[key] = self._clock.monotonic()

        if self._dispatcher is not None:
            try:
                await _dispatch_quota_alert(
                    self._dispatcher,
                    snap=snap,
                    level=level,
                    usage_pct=usage_pct,
                )
            except Exception as exc:
                reraise_critical(exc)
                log_exception_redacted(
                    logger, QUOTA_POLL_FAILED, exc, reason="quota_alert_dispatch_failed"
                )


def _compute_usage_pct(snap: QuotaSnapshot) -> float | None:
    """Compute the max usage percentage across request and token dimensions.

    Dimensions with a zero limit (unlimited) are skipped.  Returns
    ``None`` when all dimensions are unlimited.

    Args:
        snap: Quota snapshot to evaluate.

    Returns:
        Max usage percentage in [0, 100], or ``None`` if unlimited.
    """
    pcts: list[float] = []
    if snap.requests_limit > 0:
        pcts.append(100.0 * snap.requests_used / snap.requests_limit)
    if snap.tokens_limit > 0:
        pcts.append(100.0 * snap.tokens_used / snap.tokens_limit)
    return max(pcts) if pcts else None


async def _dispatch_quota_alert(
    dispatcher: NotificationDispatcher,
    *,
    snap: QuotaSnapshot,
    level: str,
    usage_pct: float,
) -> None:
    """Dispatch a quota threshold notification.

    Args:
        dispatcher: Notification dispatcher.
        snap: Quota snapshot that triggered the alert.
        level: Alert level string (``"warning"`` or ``"critical"``).
        usage_pct: Observed usage percentage.
    """
    from synthorg.notifications.models import (  # noqa: PLC0415
        Notification,
        NotificationCategory,
        NotificationSeverity,
    )

    body = (
        f"Provider {snap.provider_name!r} {snap.window.value} window is "
        f"{usage_pct:.1f}% consumed."
    )
    await dispatcher.dispatch(
        Notification(
            category=NotificationCategory.BUDGET,
            severity=NotificationSeverity(level),
            title=f"Quota {level} alert",
            body=body,
            source="budget.quota_poller",
        ),
    )
