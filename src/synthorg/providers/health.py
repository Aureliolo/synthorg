"""Provider health tracking -- models and in-memory tracker.

Records individual provider call outcomes and aggregates them
into health summaries for the API layer.
"""

import asyncio
import math
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.provider import (
    PROVIDER_HEALTH_AUTO_PRUNED,
    PROVIDER_HEALTH_CLEARED,
    PROVIDER_HEALTH_PRUNED,
)

logger = get_logger(__name__)

_HEALTH_WINDOW_HOURS: Final[int] = 24
_DEGRADED_THRESHOLD: Final[float] = 10.0  # error_rate >= 10% -> DEGRADED
_DOWN_THRESHOLD: Final[float] = 50.0  # error_rate >= 50% -> DOWN
_AUTO_PRUNE_THRESHOLD: Final[int] = 100_000


class ProviderHealthStatus(StrEnum):
    """Provider health status derived from recent error rate."""

    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class ProviderHealthRecord(BaseModel):
    """Single provider call outcome.

    Attributes:
        provider_name: Name of the provider.
        timestamp: When the call occurred.
        success: Whether the call succeeded.
        response_time_ms: Call response time in milliseconds.
        error_message: Error description when ``success`` is False.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider_name: NotBlankStr = Field(description="Provider name")
    timestamp: AwareDatetime = Field(description="When the call occurred")
    success: bool = Field(description="Whether the call succeeded")
    response_time_ms: float = Field(
        ge=0.0,
        description="Response time in milliseconds",
    )
    error_message: NotBlankStr | None = Field(
        default=None,
        max_length=1024,
        description="Error description when success is False",
    )

    @model_validator(mode="after")
    def _validate_error_consistency(self) -> Self:
        """Ensure error_message consistency with success flag.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``success=True`` but ``error_message`` is set,
                or ``success=False`` but ``error_message`` is ``None``.
        """
        if self.success and self.error_message is not None:
            msg = "error_message must be None when success is True"
            raise ValueError(msg)
        if not self.success and self.error_message is None:
            msg = "error_message must be provided when success is False"
            raise ValueError(msg)
        return self


class ProviderHealthSummary(BaseModel):
    """Aggregated provider health for API response.

    ``total_tokens_24h`` and ``total_cost_24h`` default to 0 from
    :func:`_aggregate_records` and are populated externally via
    ``model_copy(update=...)`` by the provider controller's usage
    enrichment step.

    Attributes:
        last_check_timestamp: Most recent call timestamp.
        avg_response_time_ms: Average response time over the last 24h.
        error_rate_percent_24h: Error rate percentage over the last 24h.
        calls_last_24h: Total calls in the last 24h.
        total_tokens_24h: Total tokens (input + output) in the last 24h
            (default 0, enriched externally).
        total_cost_24h: Total cost in the last 24h (default 0, enriched
            externally).
        health_status: Derived (computed_field) from call count and
            error rate (unknown/up/degraded/down). Not a constructor
            parameter.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    last_check_timestamp: AwareDatetime | None = Field(
        default=None,
        description="Most recent call timestamp",
    )
    avg_response_time_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Average response time in ms (24h window)",
    )
    error_rate_percent_24h: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Error rate percentage (24h window)",
    )
    calls_last_24h: int = Field(
        default=0,
        ge=0,
        description="Total calls in the last 24h",
    )
    total_tokens_24h: int = Field(
        default=0,
        ge=0,
        description="Total tokens (input + output) in the last 24h",
    )
    total_cost_24h: float = Field(
        default=0.0,
        ge=0.0,
        description="Total cost in the last 24h",
    )

    @model_validator(mode="after")
    def _validate_zero_calls_consistency(self) -> Self:
        """Ensure zero calls implies no average response time.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``calls_last_24h == 0`` but
                ``avg_response_time_ms`` is set.
        """
        if self.calls_last_24h == 0 and self.avg_response_time_ms is not None:
            msg = "avg_response_time_ms must be None when calls_last_24h is 0"
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def health_status(self) -> ProviderHealthStatus:
        """Derive health status from call count and error rate."""
        if self.calls_last_24h == 0:
            return ProviderHealthStatus.UNKNOWN
        return _derive_health_status(self.error_rate_percent_24h)


def _derive_health_status(error_rate: float) -> ProviderHealthStatus:
    """Derive health status from error rate percentage.

    Returns:
        The ``ProviderHealthStatus`` (``UP``, ``DEGRADED``, or ``DOWN``)
        for the given error rate.
    """
    if error_rate >= _DOWN_THRESHOLD:
        return ProviderHealthStatus.DOWN
    if error_rate >= _DEGRADED_THRESHOLD:
        return ProviderHealthStatus.DEGRADED
    return ProviderHealthStatus.UP


def _aggregate_records(
    records: list[ProviderHealthRecord],
) -> ProviderHealthSummary:
    """Aggregate a non-empty list of health records into a summary.

    Args:
        records: Non-empty list of health records (ZeroDivisionError
            if empty -- callers must pre-check).

    Returns:
        Aggregated health summary.
    """
    total = len(records)
    errors = sum(1 for r in records if not r.success)
    error_rate = round(errors / total * 100, 2)
    avg_rt = round(
        math.fsum(r.response_time_ms for r in records) / total,
        2,
    )
    last_ts = max(r.timestamp for r in records)
    return ProviderHealthSummary(
        last_check_timestamp=last_ts,
        avg_response_time_ms=avg_rt,
        error_rate_percent_24h=error_rate,
        calls_last_24h=total,
    )


class ProviderHealthTracker:
    """In-memory tracker for provider call outcomes with TTL-based eviction.

    Concurrency-safe via ``asyncio.Lock``.  Follows the same
    TTL-based eviction pattern as
    :class:`~synthorg.budget.tracker.CostTracker`: memory is bounded by
    a soft auto-prune that removes records older than 24 hours when the
    record count exceeds *auto_prune_threshold*.

    Args:
        auto_prune_threshold: Maximum record count before auto-pruning
            is triggered on snapshot.  Defaults to 100,000.

    Raises:
        ValueError: If *auto_prune_threshold* < 1.
    """

    __slots__ = ("_auto_prune_threshold", "_lock", "_records")

    def __init__(
        self,
        *,
        auto_prune_threshold: int = _AUTO_PRUNE_THRESHOLD,
    ) -> None:
        if auto_prune_threshold < 1:
            msg = f"auto_prune_threshold must be >= 1, got {auto_prune_threshold}"
            raise ValueError(msg)
        self._records: list[ProviderHealthRecord] = []
        self._lock = asyncio.Lock()
        self._auto_prune_threshold = auto_prune_threshold

    def clear(self) -> None:
        """Reset all health records for test isolation."""
        cleared_count = len(self._records)
        self._records.clear()
        logger.info(PROVIDER_HEALTH_CLEARED, cleared_count=cleared_count)

    async def record(self, record: ProviderHealthRecord) -> None:
        """Append a health record.

        Args:
            record: Immutable call outcome record.
        """
        async with self._lock:
            self._records.append(record)

    async def prune_expired(self, *, now: datetime | None = None) -> int:
        """Remove records older than the 24-hour health window.

        Call periodically from long-running services to bound
        memory growth.

        Args:
            now: Reference time.  Defaults to current UTC time.

        Returns:
            Number of records removed.
        """
        ref = now or datetime.now(UTC)
        cutoff = ref - timedelta(hours=_HEALTH_WINDOW_HOURS)
        async with self._lock:
            pruned = self._prune_before(cutoff)
            if pruned:
                logger.info(
                    PROVIDER_HEALTH_PRUNED,
                    pruned=pruned,
                    remaining=len(self._records),
                )
            return pruned

    async def get_summary(
        self,
        provider_name: str,
        *,
        now: datetime | None = None,
    ) -> ProviderHealthSummary:
        """Build an aggregated health summary for a provider.

        Only considers records within the last 24 hours.

        Args:
            provider_name: Provider to summarise.
            now: Reference time for the 24h window.  Defaults to
                current UTC time.

        Returns:
            Aggregated health summary.
        """
        ref = now or datetime.now(UTC)
        cutoff = ref - timedelta(hours=_HEALTH_WINDOW_HOURS)

        snapshot = await self._snapshot(now=ref)
        recent = [
            r
            for r in snapshot
            if r.provider_name == provider_name and cutoff <= r.timestamp <= ref
        ]

        if not recent:
            return ProviderHealthSummary()

        return _aggregate_records(recent)

    async def are_all_reachable(
        self,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Return True when no tracked provider is currently DOWN.

        Used by the /readyz probe to gate traffic. Providers whose
        recent call window contains too many failures derive a
        :attr:`ProviderHealthStatus.DOWN` status; any single one of
        those flips the reachability bit. ``DEGRADED`` providers stay
        reachable because partial traffic is preferable to a full
        outage; ``UNKNOWN`` (no recent calls) is also treated as
        reachable so a fresh boot never reports unready before the
        first provider call lands.
        """
        summaries = await self.get_all_summaries(now=now)
        return not any(
            summary.health_status is ProviderHealthStatus.DOWN
            for summary in summaries.values()
        )

    async def get_all_summaries(
        self,
        *,
        now: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Mapping[str, ProviderHealthSummary]:
        """Build summaries for all known providers, optionally paginated.

        Args:
            now: Reference time for the 24h window.
            limit: Maximum providers to include (``None`` for unbounded;
                preserves the historical contract used by callers that
                need every provider's status, e.g. the readiness probe).
            offset: Page offset honoured only when ``limit`` is set.

        Returns:
            Immutable mapping of provider name to health summary,
            wrapped in :class:`types.MappingProxyType` so callers
            cannot mutate the aggregate view.
        """
        ref = now or datetime.now(UTC)
        cutoff = ref - timedelta(hours=_HEALTH_WINDOW_HOURS)

        snapshot = await self._snapshot(now=ref)
        by_provider: dict[str, list[ProviderHealthRecord]] = defaultdict(list)
        for r in snapshot:
            if cutoff <= r.timestamp <= ref:
                by_provider[r.provider_name].append(r)

        items = sorted(by_provider.items())
        if limit is not None:
            offset = max(0, offset)
            end = offset + max(0, limit)
            items = items[offset:end]
        return MappingProxyType(
            {name: _aggregate_records(records) for name, records in items}
        )

    async def count_all_summaries(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        """Return the count of providers with records inside the 24h window.

        Companion to :meth:`get_all_summaries` for paginated controllers
        that need a total alongside the page.
        """
        ref = now or datetime.now(UTC)
        cutoff = ref - timedelta(hours=_HEALTH_WINDOW_HOURS)
        snapshot = await self._snapshot(now=ref)
        names = {r.provider_name for r in snapshot if cutoff <= r.timestamp <= ref}
        return len(names)

    async def _snapshot(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[ProviderHealthRecord, ...]:
        """Return an immutable snapshot of all current records.

        When the record count exceeds the auto-prune threshold,
        expired records are removed before the snapshot is taken.

        Args:
            now: Reference time for auto-prune cutoff.  Defaults to
                current UTC time.

        Returns:
            Immutable tuple of all current health records.
        """
        async with self._lock:
            if len(self._records) > self._auto_prune_threshold:
                ref = now or datetime.now(UTC)
                cutoff = ref - timedelta(hours=_HEALTH_WINDOW_HOURS)
                pruned = self._prune_before(cutoff)
                if pruned:
                    logger.info(
                        PROVIDER_HEALTH_AUTO_PRUNED,
                        pruned=pruned,
                        remaining=len(self._records),
                    )
            return tuple(self._records)

    def _prune_before(self, cutoff: datetime) -> int:
        """Remove records older than *cutoff*.  Caller must hold ``_lock``.

        Returns:
            The number of records removed that were older than *cutoff*.
        """
        if not self._records:
            return 0
        before = len(self._records)
        self._records = [r for r in self._records if r.timestamp >= cutoff]
        return before - len(self._records)
