# module-kind: code
"""Whether a model serves work, as opposed to whether it answers.

:mod:`synthorg.providers.health` answers reachability over a day. This
answers serviceability over a recent window, per ``(provider, model)``, and
the two disagree exactly when it matters: a model returning 503 on most
completions for an hour is reachable, and its 24-hour error rate is still
low enough to read healthy.

Three things follow from that, and each is a design decision rather than a
detail:

- the window is short, because the question is "is this usable now";
- the outcome split is by class, because "queueing" and "balance empty" are
  different operator actions wearing the same error rate;
- latency is a distribution, because a mean over one fast call and one
  five-minute call reports a number neither call took.

Nothing here holds state; :mod:`synthorg.providers.health_tracker`
accumulates the records and calls in.
"""

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderHealthStatus,
    ProviderOutcomeClass,
    RecordSource,
)

#: Outcomes that mean the pair cannot serve until a human acts. A pair
#: carrying one of these is DOWN however healthy the rest of the window
#: looks: averaging an empty balance against successes reports a pair as
#: usable while every billed call is refused. These are honoured over a
#: longer span than the rate window and cleared by a later success rather
#: than by time; see :func:`latched_failure`.
LATCHING_OUTCOMES: Final[frozenset[ProviderOutcomeClass]] = frozenset(
    {ProviderOutcomeClass.PAYMENT_REQUIRED}
)

_PERCENT: Final[float] = 100.0

#: Fifteen minutes. Short enough that an outage starting an hour ago has
#: dominated it, which is the whole point: the 24-hour health window read
#: healthy through an hour of 503s because 24 hours is mostly not now.
DEFAULT_WINDOW_SECONDS: Final[float] = 900.0

#: Matches the health summary's own boundaries, so the two surfaces disagree
#: only because they cover different spans, never because they score
#: differently.
DEFAULT_DEGRADED_ERROR_RATE_PERCENT: Final[float] = 10.0
DEFAULT_DOWN_ERROR_RATE_PERCENT: Final[float] = 50.0

#: Three calls before a verdict is anything but UNKNOWN. One failure is a
#: blip; taking a pair (and, downstream, every agent bound to it) out of
#: service on it would make the feature worse than the gap it fills.
DEFAULT_MIN_CALLS_FOR_VERDICT: Final[int] = 3

#: Twenty-four hours. How far back a latching failure is honoured, which is
#: necessarily longer than the rate window: a 402 that expired with the rate
#: window would take every agent on the pair out of service, thereby stop the
#: calls that are its own evidence, and read clear again fifteen minutes
#: later. That loop re-admits the agents, bills another refused call, and
#: repeats forever. A latch also cannot stand indefinitely, or an operator
#: who tops the account up has no way to be believed short of a restart, so
#: this doubles as the retry-after: past it, the pair is tried once more.
DEFAULT_LATCH_LOOKBACK_SECONDS: Final[float] = 86400.0


class ServiceabilityThresholds(BaseModel):
    """Where the verdict boundaries sit, and how much evidence it needs.

    Attributes:
        window_seconds: Trailing span the verdict is computed over.
        degraded_error_rate_percent: Failure rate at or above which the
            pair reads DEGRADED.
        down_error_rate_percent: Failure rate at or above which it reads
            DOWN.
        min_calls_for_verdict: Calls required before a verdict is anything
            but UNKNOWN. Below it the window withholds judgement rather
            than letting a single failure take a pair out of service.
        latch_lookback_seconds: How far back a latching failure still counts.
            Longer than ``window_seconds`` on purpose, and validated so.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    window_seconds: float = Field(default=DEFAULT_WINDOW_SECONDS, gt=0.0)
    degraded_error_rate_percent: float = Field(
        default=DEFAULT_DEGRADED_ERROR_RATE_PERCENT, ge=0.0, le=_PERCENT
    )
    down_error_rate_percent: float = Field(
        default=DEFAULT_DOWN_ERROR_RATE_PERCENT, ge=0.0, le=_PERCENT
    )
    min_calls_for_verdict: int = Field(default=DEFAULT_MIN_CALLS_FOR_VERDICT, ge=1)
    latch_lookback_seconds: float = Field(
        default=DEFAULT_LATCH_LOOKBACK_SECONDS, gt=0.0
    )

    @model_validator(mode="after")
    def _boundaries_are_ordered(self) -> Self:
        """Reject boundaries that cannot mean what they are named.

        Both orderings are separately configurable, so nothing but this
        stops a pair of values that silently disables a verdict: a DEGRADED
        boundary above the DOWN one makes DEGRADED unreachable (every rate
        high enough to degrade already reads DOWN), and a latch expiring no
        later than the rate window restores the exact decay the latch
        exists to prevent.

        Returns:
            The validated instance.

        Raises:
            ValueError: When either ordering is violated.
        """
        if self.degraded_error_rate_percent > self.down_error_rate_percent:
            msg = (
                "degraded_error_rate_percent must not exceed "
                "down_error_rate_percent, or no rate ever reads DEGRADED."
            )
            raise ValueError(msg)
        if self.latch_lookback_seconds <= self.window_seconds:
            msg = (
                "latch_lookback_seconds must exceed window_seconds: a "
                "latching failure that expires with the rate window does "
                "not latch."
            )
            raise ValueError(msg)
        return self


DEFAULT_THRESHOLDS: Final[ServiceabilityThresholds] = ServiceabilityThresholds()


class LatencyDistribution(BaseModel):
    """Where a window's latencies actually landed.

    Attributes:
        p50_ms: Median round-trip time.
        p90_ms: Ninetieth percentile.
        p99_ms: Ninety-ninth percentile.
        max_ms: Slowest call in the window.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    p50_ms: float = Field(ge=0.0)
    p90_ms: float = Field(ge=0.0)
    p99_ms: float = Field(ge=0.0)
    max_ms: float = Field(ge=0.0)


class ModelServiceability(BaseModel):
    """One ``(provider, model)`` pair's recent behaviour.

    Attributes:
        provider_name: Connection the calls went out on.
        model: Model they named, or ``None`` for a provider-wide view.
        window_seconds: Span this verdict covers.
        call_count: Real calls inside the window.
        outcome_counts: Count per outcome class; absent classes are absent
            rather than zero, so a reader can tell "did not happen" from
            "happened zero times", and the values sum to ``call_count``.
        latency: Distribution over the window, or ``None`` when empty.
        last_call_timestamp: Most recent real call in the window.
        first_failure_timestamp: Oldest failing call in the window, which is
            how long the trouble has been running as far as this window can
            see. Reported rather than derived from the counts because "since
            when" is the first thing asked of a pair that has gone down, and
            the counts cannot answer it.
        latched_failure: The outstanding latching failure, or ``None``.
            Carried explicitly rather than read off ``outcome_counts``,
            because the counts cover the rate window and this deliberately
            outlives it: a 402 that decayed with the window would take the
            pair's agents out, starve the pair of the calls that are its own
            evidence, and clear itself one window later.
        latched_since: When that latching failure was recorded.
        verdict: Derived; never a constructor parameter.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider_name: NotBlankStr
    model: NotBlankStr | None = Field(default=None)
    window_seconds: float = Field(gt=0.0)
    call_count: int = Field(default=0, ge=0)
    outcome_counts: Mapping[ProviderOutcomeClass, int] = Field(default_factory=dict)
    latency: LatencyDistribution | None = Field(default=None)
    last_call_timestamp: datetime | None = Field(default=None)
    first_failure_timestamp: datetime | None = Field(default=None)
    latched_failure: ProviderOutcomeClass | None = Field(default=None)
    latched_since: datetime | None = Field(default=None)
    # Carried on the view rather than looked up again, so the verdict a
    # reader sees is derived from the boundaries that produced its counts,
    # not from whatever the settings say by the time it is rendered.
    degraded_error_rate_percent: float = Field(
        default=DEFAULT_DEGRADED_ERROR_RATE_PERCENT, ge=0.0, le=_PERCENT
    )
    down_error_rate_percent: float = Field(
        default=DEFAULT_DOWN_ERROR_RATE_PERCENT, ge=0.0, le=_PERCENT
    )
    min_calls_for_verdict: int = Field(default=DEFAULT_MIN_CALLS_FOR_VERDICT, ge=1)

    @computed_field
    @property
    def error_rate_percent(self) -> float:
        """Share of the window's calls that did not succeed."""
        if self.call_count == 0:
            return 0.0
        succeeded = self.outcome_counts.get(ProviderOutcomeClass.SUCCESS, 0)
        return round((self.call_count - succeeded) / self.call_count * _PERCENT, 2)

    @computed_field
    @property
    def has_latching_failure(self) -> bool:
        """Whether an unresolved failure no retry can clear is outstanding."""
        return self.latched_failure is not None

    @computed_field
    @property
    def verdict(self) -> ProviderHealthStatus:
        """Derive the recent-window verdict.

        A latching outcome wins outright, ahead of the sample-size floor: a
        402 is not a statistical signal that needs corroborating, it is a
        refusal that stands until someone pays.
        """
        if self.has_latching_failure:
            return ProviderHealthStatus.DOWN
        if self.call_count < self.min_calls_for_verdict:
            return ProviderHealthStatus.UNKNOWN
        rate = self.error_rate_percent
        if rate >= self.down_error_rate_percent:
            return ProviderHealthStatus.DOWN
        if rate >= self.degraded_error_rate_percent:
            return ProviderHealthStatus.DEGRADED
        return ProviderHealthStatus.UP


def latched_failure(
    records: Sequence[ProviderHealthRecord],
    *,
    now: datetime,
    lookback_seconds: float,
) -> tuple[ProviderOutcomeClass, datetime] | None:
    """Return the outstanding latching failure for a pair, if any.

    A latching outcome is a refusal rather than a rate, so it is honoured
    over this lookback rather than over the rate window. That difference is
    the whole point: expiring with the rate window, a 402 would take the
    pair's agents out of service, thereby stop the calls that are its own
    evidence, and read clear one window later, re-admitting the agents to
    be refused again on a loop nothing breaks.

    A later success deliberately does NOT clear it. A provider that serves
    a cached or free request after refusing a billed one has not been
    topped up, so treating any success as proof would hand the latch back
    to exactly the traffic it is meant to stop.

    The lookback is therefore the sole exit, and doubles as a retry-after:
    a pair cannot be locked out permanently on one refusal, because past
    the lookback it is simply tried once more. An operator who has topped
    up and does not want to wait can shorten
    ``serviceability_latch_lookback_seconds``, which is read live.

    Args:
        records: Every retained outcome for one pair, in any order.
        now: Reference time the lookback is measured back from.
        lookback_seconds: How far back a latching failure still counts.

    Returns:
        The outstanding failure class and when it was recorded, or ``None``
        when the pair carries no unresolved latching failure.
    """
    cutoff = now - timedelta(seconds=lookback_seconds)
    latest = max(
        (
            record
            for record in records
            if record.source is RecordSource.REAL_CALL
            and record.outcome_class in LATCHING_OUTCOMES
            and cutoff <= record.timestamp <= now
        ),
        key=lambda record: record.timestamp,
        default=None,
    )
    if latest is None:
        return None
    return latest.outcome_class, latest.timestamp


def dominant_failure(view: ModelServiceability) -> ProviderOutcomeClass | None:
    """Return the failure class that decided *view*, if one did.

    A latching outcome wins outright for the same reason it wins the verdict:
    it is a refusal, not a rate. Otherwise the most-counted failure class is
    the one an operator is looking at, with ties broken by the enum's
    declaration order so the answer does not move between reads of the same
    window.

    Returns:
        The dominant failure class, or ``None`` when the window holds no
        failures at all.
    """
    if view.latched_failure is not None:
        return view.latched_failure
    failures = [
        (outcome, count)
        for outcome, count in view.outcome_counts.items()
        if outcome is not ProviderOutcomeClass.SUCCESS and count > 0
    ]
    if not failures:
        return None
    order = list(ProviderOutcomeClass)
    return max(failures, key=lambda pair: (pair[1], -order.index(pair[0])))[0]


def percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """Return the *fraction* percentile of an ascending, non-empty sequence.

    Nearest-rank rather than interpolated: every reported figure is then a
    latency some call actually took, which is what an operator comparing it
    against a timeout needs.

    Args:
        sorted_values: Ascending, non-empty latencies.
        fraction: Percentile position in ``[0, 1]``.

    Returns:
        The value at the nearest rank.
    """
    rank = math.ceil(fraction * len(sorted_values))
    index = min(max(rank, 1), len(sorted_values)) - 1
    return sorted_values[index]


def _distribution(latencies: list[float]) -> LatencyDistribution | None:
    """Build the latency distribution for a window.

    Returns:
        The distribution, or ``None`` when the window is empty.
    """
    if not latencies:
        return None
    ordered = sorted(latencies)
    return LatencyDistribution(
        p50_ms=percentile(ordered, 0.50),
        p90_ms=percentile(ordered, 0.90),
        p99_ms=percentile(ordered, 0.99),
        max_ms=ordered[-1],
    )


def aggregate_serviceability(
    records: Sequence[ProviderHealthRecord],
    *,
    now: datetime,
    thresholds: ServiceabilityThresholds = DEFAULT_THRESHOLDS,
    provider_name: str | None = None,
    model: str | None = None,
) -> ModelServiceability:
    """Summarise *records* over the recent window.

    Probe records are excluded: a probe measures whether an endpoint
    answers, and letting a healthy probe cadence dilute a failing model's
    error rate is the specific way the existing surface reported a
    503-on-most-calls model as healthy.

    Args:
        records: Outcomes for one pair, in any order.
        now: Reference time the window is measured back from.
        thresholds: Verdict boundaries and the evidence floor.
        provider_name: Provider to report; taken from the records when
            omitted.
        model: Model to report; taken from the records when omitted.

    Returns:
        The pair's recent-window view.
    """
    cutoff = now - timedelta(seconds=thresholds.window_seconds)
    recent = [
        record
        for record in records
        if record.source is RecordSource.REAL_CALL and cutoff <= record.timestamp <= now
    ]

    counts: dict[ProviderOutcomeClass, int] = {}
    for record in recent:
        counts[record.outcome_class] = counts.get(record.outcome_class, 0) + 1

    latched = latched_failure(
        records,
        now=now,
        lookback_seconds=thresholds.latch_lookback_seconds,
    )
    resolved_provider = provider_name or _first_provider(records)
    return ModelServiceability(
        provider_name=NotBlankStr(resolved_provider),
        model=_resolved_model(model, recent),
        window_seconds=thresholds.window_seconds,
        call_count=len(recent),
        outcome_counts=MappingProxyType(counts),
        latency=_distribution([r.response_time_ms for r in recent]),
        last_call_timestamp=max((r.timestamp for r in recent), default=None),
        first_failure_timestamp=min(
            (
                r.timestamp
                for r in recent
                if r.outcome_class is not ProviderOutcomeClass.SUCCESS
            ),
            default=None,
        ),
        latched_failure=latched[0] if latched else None,
        latched_since=latched[1] if latched else None,
        degraded_error_rate_percent=thresholds.degraded_error_rate_percent,
        down_error_rate_percent=thresholds.down_error_rate_percent,
        min_calls_for_verdict=thresholds.min_calls_for_verdict,
    )


def _first_provider(records: Sequence[ProviderHealthRecord]) -> str:
    """Return the provider these records belong to.

    Returns:
        The first record's provider name, or a placeholder when the caller
        supplied neither records nor a name (an empty window still has to
        report which pair it is empty for).
    """
    return records[0].provider_name if records else "unknown"


def _resolved_model(
    model: str | None, recent: Sequence[ProviderHealthRecord]
) -> NotBlankStr | None:
    """Return the model to report on the aggregate.

    Returns:
        The explicit *model*, else the first record's, else ``None``.
    """
    if model is not None:
        return NotBlankStr(model)
    for record in recent:
        if record.model is not None:
            return record.model
    return None


__all__ = [
    "DEFAULT_DEGRADED_ERROR_RATE_PERCENT",
    "DEFAULT_DOWN_ERROR_RATE_PERCENT",
    "DEFAULT_LATCH_LOOKBACK_SECONDS",
    "DEFAULT_MIN_CALLS_FOR_VERDICT",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_WINDOW_SECONDS",
    "LATCHING_OUTCOMES",
    "LatencyDistribution",
    "ModelServiceability",
    "ServiceabilityThresholds",
    "aggregate_serviceability",
    "dominant_failure",
    "latched_failure",
    "percentile",
]
