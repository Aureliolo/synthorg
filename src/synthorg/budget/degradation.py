"""Quota degradation resolution.

Implements the QUEUE and ALERT degradation strategies for provider quota
exhaustion.  Called by :class:`~synthorg.budget.enforcer.BudgetEnforcer`
when a pre-flight quota check fails and degradation resolution is needed.

Neither strategy moves the caller onto a different connection. A provider is
a registered connection with its own credentials, endpoint and quota, so
re-pointing an agent at another one mid-dispatch would run the operator's
choice on a connection nobody chose and bill a quota nobody named. QUEUE
waits for the same provider's window to rotate; ALERT refuses. An agent whose
provider stays out is marked unavailable by the roster and its work is
reassigned, which is the org's answer rather than the dispatch's.
"""

import asyncio
from datetime import UTC, datetime
from typing import NoReturn

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.errors import QuotaExhaustedError
from synthorg.budget.quota import (
    DegradationAction,
    DegradationConfig,
    QuotaCheckResult,
    QuotaSnapshot,
    QuotaWindow,
)
from synthorg.budget.quota_tracker import QuotaTracker
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.degradation import (
    DEGRADATION_ALERT_RAISED,
    DEGRADATION_QUEUE_EXHAUSTED,
    DEGRADATION_QUEUE_RESUMED,
    DEGRADATION_QUEUE_STARTED,
    DEGRADATION_QUEUE_WAITING,
    DEGRADATION_QUEUE_WINDOW_ROTATED,
)

logger = get_logger(__name__)

# Alias for testability (tests patch this to avoid real sleeps).
asyncio_sleep = asyncio.sleep


# ── Result models ─────────────────────────────────────────────────


class DegradationResult(BaseModel):
    """Result of quota degradation resolution.

    Attributes:
        provider: The provider whose quota was exhausted, and which the
            caller still dispatches to: degradation waits, it never
            re-points.
        action_taken: Which degradation action was applied.
        wait_seconds: Seconds the QUEUE strategy waited.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(
        description="Provider that was quota-exhausted and then waited for",
    )
    action_taken: DegradationAction = Field(
        description="Degradation action that was applied",
    )
    wait_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Seconds waited",
    )


class PreFlightResult(BaseModel):
    """Result of pre-flight budget enforcement.

    Attributes:
        degradation: Degradation result when degradation was triggered.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    degradation: DegradationResult | None = Field(
        default=None,
        description="Degradation result (None if not triggered)",
    )


# ── Public API ────────────────────────────────────────────────────


async def resolve_degradation(
    *,
    provider_name: str,
    quota_result: QuotaCheckResult,
    degradation_config: DegradationConfig,
    quota_tracker: QuotaTracker,
    estimated_tokens: int = 0,
) -> DegradationResult:
    """Resolve a quota exhaustion using the configured strategy.

    Args:
        provider_name: The exhausted provider.
        quota_result: The denied quota check result.
        degradation_config: Degradation configuration for the provider.
        quota_tracker: Quota tracker for re-checking after the wait.
        estimated_tokens: Estimated tokens for the upcoming request.

    Returns:
        Degradation result recording the wait.

    Raises:
        QuotaExhaustedError: When the strategy cannot resolve.
    """
    if degradation_config.strategy is DegradationAction.QUEUE:
        return await _resolve_queue(
            provider_name=provider_name,
            quota_result=quota_result,
            degradation_config=degradation_config,
            quota_tracker=quota_tracker,
            estimated_tokens=estimated_tokens,
        )

    # ALERT (default) -- raise immediately
    logger.warning(
        DEGRADATION_ALERT_RAISED,
        provider=provider_name,
        reason=quota_result.reason,
    )
    msg = f"Provider {provider_name!r} quota exhausted: {quota_result.reason}"
    raise QuotaExhaustedError(
        msg,
        provider_name=provider_name,
        degradation_action=DegradationAction.ALERT,
    )


# ── QUEUE ─────────────────────────────────────────────────────────


async def _resolve_queue(
    *,
    provider_name: str,
    quota_result: QuotaCheckResult,
    degradation_config: DegradationConfig,
    quota_tracker: QuotaTracker,
    estimated_tokens: int = 0,
) -> DegradationResult:
    """Wait for the shortest quota window to reset, then re-check.

    Returns:
        A QUEUE-action ``DegradationResult`` once the post-wait re-check
        confirms quota is available again.

    Raises:
        QuotaExhaustedError: When wait exceeds max, no reset time,
            or still exhausted after waiting.
    """
    max_wait = degradation_config.queue_max_wait_seconds
    logger.info(
        DEGRADATION_QUEUE_STARTED,
        provider=provider_name,
        max_wait_seconds=max_wait,
    )

    delay = await _compute_queue_delay(
        provider_name=provider_name,
        exhausted_windows=quota_result.exhausted_windows,
        quota_tracker=quota_tracker,
        max_wait=max_wait,
    )

    if delay > 0:
        logger.info(
            DEGRADATION_QUEUE_WAITING,
            provider=provider_name,
            delay_seconds=delay,
        )
        await asyncio_sleep(delay)

    return await _recheck_after_wait(
        provider_name,
        quota_tracker,
        estimated_tokens,
        delay,
    )


async def _recheck_after_wait(
    provider_name: str,
    quota_tracker: QuotaTracker,
    estimated_tokens: int,
    delay: float,
) -> DegradationResult:
    """Re-check quota after waiting; raise if still exhausted.

    Returns:
        A QUEUE-action ``DegradationResult`` recording the wait, once the
        re-check confirms quota is available.

    Raises:
        QuotaExhaustedError: If the relevant budget or quota is exhausted.
    """
    recheck = await quota_tracker.check_quota(
        provider_name,
        estimated_tokens=estimated_tokens,
    )
    if not recheck.allowed:
        logger.warning(
            DEGRADATION_QUEUE_EXHAUSTED,
            provider=provider_name,
            reason="still_exhausted_after_wait",
        )
        msg = f"Provider {provider_name!r} still exhausted after waiting {delay:.1f}s"
        raise QuotaExhaustedError(
            msg,
            provider_name=provider_name,
            degradation_action=DegradationAction.QUEUE,
        )

    logger.info(
        DEGRADATION_QUEUE_RESUMED,
        provider=provider_name,
        wait_seconds=delay,
    )
    return DegradationResult(
        provider=NotBlankStr(provider_name),
        action_taken=DegradationAction.QUEUE,
        wait_seconds=delay,
    )


async def _compute_queue_delay(
    *,
    provider_name: str,
    exhausted_windows: tuple[QuotaWindow, ...],
    quota_tracker: QuotaTracker,
    max_wait: int,
) -> float:
    """Compute delay until the soonest window reset.

    Returns:
        Seconds to wait until the soonest exhausted-window reset, or
        ``0.0`` when that window has already rotated.

    Raises:
        QuotaExhaustedError: When no reset time is available, or the
            computed delay exceeds ``max_wait``.
    """
    snapshots = await quota_tracker.get_snapshot(provider_name)
    reset_times = _extract_reset_times(snapshots, exhausted_windows)

    if not reset_times:
        msg = f"Provider {provider_name!r} quota exhausted but no reset time available"
        _queue_exhausted_error(
            provider_name,
            msg,
            reason="no_reset_time_available",
        )

    soonest = min(reset_times)
    delay = (soonest - datetime.now(UTC)).total_seconds()

    if delay <= 0:
        logger.debug(
            DEGRADATION_QUEUE_WINDOW_ROTATED,
            provider=provider_name,
        )
        return 0.0

    if delay > max_wait:
        msg = (
            f"Provider {provider_name!r} quota reset in "
            f"{delay:.0f}s exceeds max wait {max_wait}s"
        )
        _queue_exhausted_error(
            provider_name,
            msg,
            reason="max_wait_exceeded",
            delay_seconds=delay,
            max_wait_seconds=max_wait,
        )

    return delay


def _extract_reset_times(
    snapshots: tuple[QuotaSnapshot, ...],
    exhausted_windows: tuple[QuotaWindow, ...],
) -> list[datetime]:
    """Filter snapshots to exhausted windows with reset times.

    Returns:
        The reset timestamps of the exhausted windows that carry one, in
        snapshot order.
    """
    return [
        snap.window_resets_at
        for snap in snapshots
        if snap.window in exhausted_windows and snap.window_resets_at
    ]


def _queue_exhausted_error(
    provider_name: str,
    msg: str,
    *,
    reason: str = "queue_exhausted",
    **extra: object,
) -> NoReturn:
    """Log and raise the QUEUE-exhaustion error.

    Raises:
        QuotaExhaustedError: Always; the queue strategy cannot satisfy the
            request within ``max_wait``.
    """
    logger.warning(
        DEGRADATION_QUEUE_EXHAUSTED,
        provider=provider_name,
        reason=reason,
        **extra,
    )
    raise QuotaExhaustedError(
        msg,
        provider_name=provider_name,
        degradation_action=DegradationAction.QUEUE,
    )
