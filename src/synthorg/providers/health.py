# module-kind: code
"""What a provider's health is, and how a set of outcomes becomes one.

The vocabulary every health surface speaks: the record one call leaves
behind, the summary a window of them aggregates to, and the status that
summary reports. :mod:`synthorg.providers.health_tracker` accumulates the
records and calls the aggregation here; nothing in this module holds
state.
"""

import math
from collections.abc import Iterable, Sequence
from enum import StrEnum
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

#: Trailing window every aggregate is computed over. Read by the tracker
#: too, which prunes to exactly the span that can still be summarised.
HEALTH_WINDOW_HOURS: Final[int] = 24

#: How many of the newest outcomes decide whether a provider is serving.
#: Small enough that a provider which has just started failing says so
#: within a few calls, large enough that one transient blip reads DEGRADED
#: rather than DOWN.
LIVENESS_SAMPLE_SIZE: Final[int] = 5

_DEGRADED_THRESHOLD: Final[float] = 10.0  # error_rate >= 10% -> DEGRADED
_DOWN_THRESHOLD: Final[float] = 50.0  # error_rate >= 50% -> DOWN


class ProviderHealthStatus(StrEnum):
    """Whether a provider is serving, judged on its newest outcomes."""

    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class ProviderReachability(StrEnum):
    """The worst provider verdict across every tracked provider.

    Three states, not a boolean, because collapsing DEGRADED into
    "reachable" is how a provider failing two calls in five reported the
    same green as one failing none.
    """

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


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
    :func:`aggregate_records` and are populated externally via
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
        liveness_calls: How many outcomes back the ``health_status``
            verdict: the newest :data:`LIVENESS_SAMPLE_SIZE` at or after
            the provider's liveness epoch.
        liveness_error_rate_percent: Error rate across exactly those
            outcomes.
        health_status: Derived (computed_field) from the liveness fields
            alone (unknown/up/degraded/down). Not a constructor
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
    liveness_calls: int = Field(
        default=0,
        ge=0,
        description="Outcomes backing the health_status verdict",
    )
    liveness_error_rate_percent: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Error rate across the outcomes backing health_status",
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

    @computed_field
    @property
    def health_status(self) -> ProviderHealthStatus:
        """Whether the provider is serving, on its newest outcomes alone.

        Deliberately blind to ``error_rate_percent_24h``. The two answer
        different questions on different timescales, and sharing one number
        between them is what kept a recovered provider reading DOWN: a day's
        worth of failures outvotes every call since the fix, so no amount of
        success could clear the verdict.
        """
        if self.liveness_calls == 0:
            return ProviderHealthStatus.UNKNOWN
        return _derive_health_status(self.liveness_error_rate_percent)


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


def worst_reachability(
    statuses: Iterable[ProviderHealthStatus],
) -> ProviderReachability:
    """Reduce every provider's verdict to the worst one present.

    ``UNKNOWN`` does not participate: a provider nothing has called yet is
    not evidence of a problem, and treating it as one would make a fresh
    boot report trouble before the first call lands.

    Returns:
        ``DOWN`` when any provider is down, ``DEGRADED`` when any is
        degraded, otherwise ``OK``.
    """
    seen = set(statuses)
    if ProviderHealthStatus.DOWN in seen:
        return ProviderReachability.DOWN
    if ProviderHealthStatus.DEGRADED in seen:
        return ProviderReachability.DEGRADED
    return ProviderReachability.OK


def aggregate_records(
    records: list[ProviderHealthRecord],
    *,
    liveness_records: Sequence[ProviderHealthRecord],
) -> ProviderHealthSummary:
    """Aggregate a non-empty list of health records into a summary.

    Args:
        records: Non-empty list of health records inside the 24h window
            (ZeroDivisionError if empty -- callers must pre-check).
        liveness_records: The newest outcomes that decide
            ``health_status``, already narrowed by the caller to the
            provider's liveness epoch and sample size. May be empty, which
            reports ``UNKNOWN``.

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
    live_total = len(liveness_records)
    live_errors = sum(1 for r in liveness_records if not r.success)
    live_rate = round(live_errors / live_total * 100, 2) if live_total else 0.0
    return ProviderHealthSummary(
        last_check_timestamp=last_ts,
        avg_response_time_ms=avg_rt,
        error_rate_percent_24h=error_rate,
        calls_last_24h=total,
        liveness_calls=live_total,
        liveness_error_rate_percent=live_rate,
    )


__all__ = [
    "HEALTH_WINDOW_HOURS",
    "LIVENESS_SAMPLE_SIZE",
    "ProviderHealthRecord",
    "ProviderHealthStatus",
    "ProviderHealthSummary",
    "ProviderReachability",
    "aggregate_records",
    "worst_reachability",
]
