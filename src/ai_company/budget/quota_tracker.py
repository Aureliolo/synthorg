"""Quota tracking service.

Tracks per-provider request and token usage against configured quota
windows.  Window-based counters are rotated automatically when a window
boundary is crossed.

Concurrency-safe via ``asyncio.Lock`` (same pattern as
:class:`~ai_company.budget.tracker.CostTracker`).
"""

import asyncio
import copy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Mapping

from ai_company.budget.quota import (
    QuotaCheckResult,
    QuotaLimit,
    QuotaSnapshot,
    QuotaWindow,
    SubscriptionConfig,
    window_start,
)
from ai_company.observability import get_logger
from ai_company.observability.events.quota import (
    QUOTA_CHECK_ALLOWED,
    QUOTA_CHECK_DENIED,
    QUOTA_SNAPSHOT_QUERIED,
    QUOTA_TRACKER_CREATED,
    QUOTA_USAGE_RECORDED,
    QUOTA_WINDOW_ROTATED,
)

logger = get_logger(__name__)


class _WindowUsage(NamedTuple):
    """Mutable-via-replace usage counters for a single window."""

    requests: int
    tokens: int
    window_start: datetime


class QuotaTracker:
    """Tracks per-provider quota usage across configured time windows.

    Providers without a subscription config are silently ignored (no-op
    on record, always allowed on check).

    Args:
        subscriptions: Mapping of provider name to subscription config.
    """

    def __init__(
        self,
        *,
        subscriptions: Mapping[str, SubscriptionConfig],
    ) -> None:
        self._subscriptions: dict[str, SubscriptionConfig] = dict(
            copy.deepcopy(dict(subscriptions)),
        )
        self._lock = asyncio.Lock()
        self._usage: dict[str, dict[QuotaWindow, _WindowUsage]] = {}

        # Initialize usage tracking for providers with quotas
        for provider_name, sub_config in self._subscriptions.items():
            if sub_config.quotas:
                self._usage[provider_name] = {}
                for quota in sub_config.quotas:
                    ws = window_start(quota.window)
                    self._usage[provider_name][quota.window] = _WindowUsage(
                        requests=0,
                        tokens=0,
                        window_start=ws,
                    )

        logger.debug(
            QUOTA_TRACKER_CREATED,
            provider_count=len(self._subscriptions),
            tracked_providers=sorted(self._usage),
        )

    async def record_usage(
        self,
        provider_name: str,
        *,
        requests: int = 1,
        tokens: int = 0,
    ) -> None:
        """Record usage against all configured windows for a provider.

        Rotates window counters if a window boundary has been crossed.
        Providers with no subscription config are silently ignored.

        Args:
            provider_name: Provider to record usage for.
            requests: Number of requests to record.
            tokens: Number of tokens to record.
        """
        if provider_name not in self._usage:
            return

        async with self._lock:
            now = datetime.now(UTC)
            provider_usage = self._usage[provider_name]

            for window_type in list(provider_usage):
                current = provider_usage[window_type]
                expected_start = window_start(window_type, now=now)

                if expected_start != current.window_start:
                    # Window boundary crossed — rotate
                    provider_usage[window_type] = _WindowUsage(
                        requests=requests,
                        tokens=tokens,
                        window_start=expected_start,
                    )
                    logger.debug(
                        QUOTA_WINDOW_ROTATED,
                        provider=provider_name,
                        window=window_type.value,
                        old_start=str(current.window_start),
                        new_start=str(expected_start),
                    )
                else:
                    provider_usage[window_type] = _WindowUsage(
                        requests=current.requests + requests,
                        tokens=current.tokens + tokens,
                        window_start=current.window_start,
                    )

            logger.debug(
                QUOTA_USAGE_RECORDED,
                provider=provider_name,
                requests=requests,
                tokens=tokens,
            )

    async def check_quota(
        self,
        provider_name: str,
        *,
        estimated_tokens: int = 0,
    ) -> QuotaCheckResult:
        """Pre-flight check: can this provider handle a request?

        Providers with no subscription config always return allowed.

        Args:
            provider_name: Provider to check.
            estimated_tokens: Estimated tokens for the request.

        Returns:
            Check result with allowed status and reason.
        """
        if provider_name not in self._usage:
            return QuotaCheckResult(
                allowed=True,
                provider_name=provider_name,
            )

        sub_config = self._subscriptions[provider_name]
        quota_map = {q.window: q for q in sub_config.quotas}

        async with self._lock:
            now = datetime.now(UTC)
            provider_usage = self._usage[provider_name]
            exhausted: list[QuotaWindow] = []
            reasons: list[str] = []

            for window_type, usage in provider_usage.items():
                expected_start = window_start(window_type, now=now)

                # If window has rotated, counters would be zero
                if expected_start != usage.window_start:
                    continue

                quota = quota_map.get(window_type)
                if quota is None:
                    continue

                if _is_window_exhausted(
                    usage,
                    quota,
                    estimated_tokens,
                ):
                    exhausted.append(window_type)
                    reasons.append(
                        _build_exhaustion_reason(
                            provider_name,
                            window_type,
                            usage,
                            quota,
                        ),
                    )

        if exhausted:
            result = QuotaCheckResult(
                allowed=False,
                provider_name=provider_name,
                reason="; ".join(reasons),
                exhausted_windows=tuple(exhausted),
            )
            logger.info(
                QUOTA_CHECK_DENIED,
                provider=provider_name,
                exhausted_windows=[w.value for w in exhausted],
                reason=result.reason,
            )
            return result

        logger.debug(
            QUOTA_CHECK_ALLOWED,
            provider=provider_name,
        )
        return QuotaCheckResult(
            allowed=True,
            provider_name=provider_name,
        )

    async def get_snapshot(
        self,
        provider_name: str,
        window: QuotaWindow | None = None,
    ) -> tuple[QuotaSnapshot, ...]:
        """Get current usage snapshots for a provider.

        Args:
            provider_name: Provider to query.
            window: Optional specific window to query. If ``None``,
                returns all windows.

        Returns:
            Tuple of quota snapshots.
        """
        if provider_name not in self._usage:
            return ()

        sub_config = self._subscriptions[provider_name]
        quota_map = {q.window: q for q in sub_config.quotas}

        async with self._lock:
            now = datetime.now(UTC)
            snapshots: list[QuotaSnapshot] = []
            provider_usage = self._usage[provider_name]

            for window_type, usage in provider_usage.items():
                if window is not None and window_type != window:
                    continue

                quota = quota_map.get(window_type)
                if quota is None:
                    continue

                expected_start = window_start(window_type, now=now)
                # If window has rotated, show zero usage
                if expected_start != usage.window_start:
                    req_used = 0
                    tok_used = 0
                else:
                    req_used = usage.requests
                    tok_used = usage.tokens

                snapshots.append(
                    QuotaSnapshot(
                        provider_name=provider_name,
                        window=window_type,
                        requests_used=req_used,
                        requests_limit=quota.max_requests,
                        tokens_used=tok_used,
                        tokens_limit=quota.max_tokens,
                        window_resets_at=None,
                        captured_at=now,
                    ),
                )

        logger.debug(
            QUOTA_SNAPSHOT_QUERIED,
            provider=provider_name,
            snapshot_count=len(snapshots),
        )
        return tuple(snapshots)

    async def get_all_snapshots(
        self,
    ) -> dict[str, tuple[QuotaSnapshot, ...]]:
        """Get usage snapshots for all tracked providers.

        Returns:
            Dict mapping provider name to tuple of snapshots.
        """
        result: dict[str, tuple[QuotaSnapshot, ...]] = {}
        for provider_name in self._usage:
            result[provider_name] = await self.get_snapshot(provider_name)
        return result


def _is_window_exhausted(
    usage: _WindowUsage,
    quota: QuotaLimit,
    estimated_tokens: int,
) -> bool:
    """Check if a window's quota is exhausted."""
    if quota.max_requests > 0 and usage.requests >= quota.max_requests:
        return True
    if quota.max_tokens > 0:
        projected = usage.tokens + estimated_tokens
        if projected >= quota.max_tokens:
            return True
    return False


def _build_exhaustion_reason(
    provider_name: str,
    window: QuotaWindow,
    usage: _WindowUsage,
    quota: QuotaLimit,
) -> str:
    """Build a human-readable exhaustion reason."""
    parts: list[str] = [f"{provider_name} {window.value}:"]
    if quota.max_requests > 0 and usage.requests >= quota.max_requests:
        parts.append(
            f"requests {usage.requests}/{quota.max_requests}",
        )
    if quota.max_tokens > 0 and usage.tokens >= quota.max_tokens:
        parts.append(
            f"tokens {usage.tokens}/{quota.max_tokens}",
        )
    return " ".join(parts)
