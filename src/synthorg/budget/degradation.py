"""Quota degradation resolution.

Implements FALLBACK, QUEUE, and ALERT degradation strategies for
provider quota exhaustion.  Called by
:class:`~synthorg.budget.enforcer.BudgetEnforcer` when a pre-flight
quota check fails and degradation resolution is needed.
"""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.budget.errors import QuotaExhaustedError
from synthorg.budget.quota import (
    DegradationAction,
    DegradationConfig,
    QuotaCheckResult,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.degradation import (
    DEGRADATION_ALERT_RAISED,
    DEGRADATION_FALLBACK_CHECK_ERROR,
    DEGRADATION_FALLBACK_EXHAUSTED,
    DEGRADATION_FALLBACK_PROVIDER_CHECKED,
    DEGRADATION_FALLBACK_RESOLVED,
    DEGRADATION_FALLBACK_STARTED,
    DEGRADATION_QUEUE_EXHAUSTED,
    DEGRADATION_QUEUE_RESUMED,
    DEGRADATION_QUEUE_STARTED,
    DEGRADATION_QUEUE_WAITING,
    DEGRADATION_QUEUE_WINDOW_ROTATED,
)

if TYPE_CHECKING:
    from synthorg.budget.quota import QuotaSnapshot, QuotaWindow
    from synthorg.budget.quota_tracker import QuotaTracker

logger = get_logger(__name__)

# Alias for testability (tests patch this to avoid real sleeps).
asyncio_sleep = asyncio.sleep


# ── Result models ─────────────────────────────────────────────────


class DegradationResult(BaseModel):
    """Result of quota degradation resolution.

    Attributes:
        original_provider: The provider whose quota was exhausted.
        effective_provider: The provider to actually use after
            degradation.
        action_taken: Which degradation action was applied.
        wait_seconds: Seconds the QUEUE strategy waited (0 for
            FALLBACK/ALERT).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    original_provider: NotBlankStr = Field(
        description="Provider that was quota-exhausted",
    )
    effective_provider: NotBlankStr = Field(
        description="Provider to use after degradation",
    )
    action_taken: DegradationAction = Field(
        description="Degradation action that was applied",
    )
    wait_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Seconds waited (0 for FALLBACK)",
    )


class PreFlightResult(BaseModel):
    """Result of pre-flight budget enforcement.

    Attributes:
        degradation: Degradation result when degradation was triggered.
        effective_provider: Derived from ``degradation`` -- the
            provider to use, or ``None`` when no degradation occurred.
            For QUEUE, equals the original provider (quota was
            re-checked after waiting).  For FALLBACK, this is the
            fallback provider.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    degradation: DegradationResult | None = Field(
        default=None,
        description="Degradation result (None if not triggered)",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_provider(self) -> str | None:
        """Provider to use after degradation, or None."""
        if self.degradation is None:
            return None
        return self.degradation.effective_provider


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
        quota_tracker: Quota tracker for checking fallback providers.
        estimated_tokens: Estimated tokens for the upcoming request.

    Returns:
        Degradation result with the effective provider.

    Raises:
        QuotaExhaustedError: When the strategy cannot resolve.
    """
    strategy = degradation_config.strategy

    if strategy == DegradationAction.FALLBACK:
        return await _resolve_fallback(
            provider_name=provider_name,
            degradation_config=degradation_config,
            quota_tracker=quota_tracker,
            estimated_tokens=estimated_tokens,
        )

    if strategy == DegradationAction.QUEUE:
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


# ── FALLBACK ──────────────────────────────────────────────────────


async def _resolve_fallback(
    *,
    provider_name: str,
    degradation_config: DegradationConfig,
    quota_tracker: QuotaTracker,
    estimated_tokens: int = 0,
) -> DegradationResult:
    """Walk the fallback provider list and return the first available.

    Raises:
        QuotaExhaustedError: When no providers configured or all
            exhausted.
    """
    fallbacks = degradation_config.fallback_providers
    if not fallbacks:
        raise _no_fallback_error(provider_name)

    logger.info(
        DEGRADATION_FALLBACK_STARTED,
        provider=provider_name,
        fallback_count=len(fallbacks),
    )

    tried: list[str] = []
    for fallback_name in fallbacks:
        try:
            check = await quota_tracker.check_quota(
                fallback_name,
                estimated_tokens=estimated_tokens,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                DEGRADATION_FALLBACK_CHECK_ERROR,
                provider=fallback_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            tried.append(fallback_name)
            continue
        logger.debug(
            DEGRADATION_FALLBACK_PROVIDER_CHECKED,
            provider=fallback_name,
            allowed=check.allowed,
        )
        if check.allowed:
            return _build_fallback_result(
                provider_name,
                fallback_name,
            )
        tried.append(fallback_name)

    raise _all_fallbacks_exhausted_error(provider_name, tried)


def _build_fallback_result(
    original: str,
    fallback: str,
) -> DegradationResult:
    """Build a FALLBACK result and log the resolution."""
    logger.info(
        DEGRADATION_FALLBACK_RESOLVED,
        original_provider=original,
        fallback_provider=fallback,
    )
    return DegradationResult(
        original_provider=original,
        effective_provider=fallback,
        action_taken=DegradationAction.FALLBACK,
    )


def _no_fallback_error(
    provider_name: str,
) -> QuotaExhaustedError:
    """Log and build error for no fallback providers configured."""
    logger.warning(
        DEGRADATION_FALLBACK_EXHAUSTED,
        provider=provider_name,
        reason="no_fallback_providers_configured",
    )
    msg = f"No fallback providers configured for provider {provider_name!r}"
    return QuotaExhaustedError(
        msg,
        provider_name=provider_name,
        degradation_action=DegradationAction.FALLBACK,
    )


def _all_fallbacks_exhausted_error(
    provider_name: str,
    tried: list[str],
) -> QuotaExhaustedError:
    """Log and build error for all fallbacks exhausted."""
    logger.warning(
        DEGRADATION_FALLBACK_EXHAUSTED,
        provider=provider_name,
        tried=tried,
    )
    msg = (
        f"All fallback providers exhausted for "
        f"provider {provider_name!r}: tried {tried}"
    )
    return QuotaExhaustedError(
        msg,
        provider_name=provider_name,
        degradation_action=DegradationAction.FALLBACK,
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
    """Re-check quota after waiting; raise if still exhausted."""
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
        original_provider=provider_name,
        effective_provider=provider_name,
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

    Returns 0.0 when the window has already rotated.

    Raises:
        QuotaExhaustedError: When no reset time is available or
            delay exceeds ``max_wait``.
    """
    snapshots = await quota_tracker.get_snapshot(provider_name)
    reset_times = _extract_reset_times(snapshots, exhausted_windows)

    if not reset_times:
        msg = f"Provider {provider_name!r} quota exhausted but no reset time available"
        raise _queue_exhausted_error(
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
        raise _queue_exhausted_error(
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
    """Filter snapshots to exhausted windows with reset times."""
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
) -> QuotaExhaustedError:
    """Log and build error for QUEUE exhaustion."""
    logger.warning(
        DEGRADATION_QUEUE_EXHAUSTED,
        provider=provider_name,
        reason=reason,
        **extra,
    )
    return QuotaExhaustedError(
        msg,
        provider_name=provider_name,
        degradation_action=DegradationAction.QUEUE,
    )
