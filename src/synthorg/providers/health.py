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
from typing import Final, Self, get_args

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.core.types import NotBlankStr
from synthorg.providers.errors import ProviderErrorLabel

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

    More than a boolean, because collapsing DEGRADED into "reachable" is how
    a provider failing two calls in five reported the same green as one
    failing none.

    ``UNKNOWN`` is never derived from provider outcomes: it is reserved for
    the reader failing to establish a verdict at all. An operator who reads
    ``down`` goes looking at endpoints and credentials, so a fault in the
    health machinery itself must not borrow that word and send them there.
    """

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class RecordSource(StrEnum):
    """What produced an outcome record.

    The two sources answer different questions and must not be averaged
    together. A probe says whether an endpoint answers; a real call says
    whether a model serves work. Mixing them lets a healthy probe cadence
    dilute a failing model's error rate, which is how a model returning 503
    on most completions read as healthy for over an hour.
    """

    REAL_CALL = "real_call"
    PROBE = "probe"


class ProviderOutcomeClass(StrEnum):
    """What happened to one call, as one closed vocabulary.

    Extends the error labels with a success member so a single mapping
    describes a window completely: the counts sum to the call count, with no
    separate success total to keep in step.
    """

    SUCCESS = "success"
    RATE_LIMIT = "rate_limit"
    QUOTA_EXCEEDED = "quota_exceeded"
    PAYMENT_REQUIRED = "payment_required"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    INTERNAL = "internal"
    OVERLOADED = "overloaded"
    INVALID_REQUEST = "invalid_request"
    AUTH = "auth"
    CONTENT_FILTER = "content_filter"
    NOT_FOUND = "not_found"
    OTHER = "other"

    @classmethod
    def for_error(cls, label: ProviderErrorLabel) -> ProviderOutcomeClass:
        """Return the outcome class for a classified provider error.

        Returns:
            The member whose value equals *label*.
        """
        return cls(label)


# Fail at import if the two vocabularies drift. ``ProviderOutcomeClass`` is
# the error labels plus a success member, so a label added to one and not the
# other would silently produce records this enum cannot represent.
_EXPECTED_OUTCOMES: Final[frozenset[str]] = frozenset(get_args(ProviderErrorLabel)) | {
    ProviderOutcomeClass.SUCCESS.value
}
_outcome_drift = _EXPECTED_OUTCOMES.symmetric_difference(
    {member.value for member in ProviderOutcomeClass}
)
if _outcome_drift:
    _msg = (
        "ProviderOutcomeClass and ProviderErrorLabel disagree on: "
        f"{sorted(_outcome_drift)}"
    )
    raise ValueError(_msg)


class ProviderHealthRecord(BaseModel):
    """Single provider call outcome.

    Attributes:
        provider_name: Name of the provider.
        model: Model the call was dispatched against; ``None`` for a
            reachability probe, which calls no model.
        timestamp: When the call occurred.
        success: Whether the call succeeded.
        outcome_class: What happened, as one closed vocabulary.
        response_time_ms: Call response time in milliseconds.
        error_message: Error description when ``success`` is False.
        source: Whether a real call or a reachability probe produced this.
        agent_id: Agent the call was attributed to, when one was in scope.
        task_id: Task the call was attributed to, when one was in scope.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider_name: NotBlankStr = Field(description="Provider name")
    model: NotBlankStr | None = Field(
        default=None,
        description="Model the call used; None for a reachability probe",
    )
    timestamp: AwareDatetime = Field(description="When the call occurred")
    success: bool = Field(description="Whether the call succeeded")
    # Derived from ``success`` when omitted rather than defaulting to a
    # constant: a fixed default contradicts half the records that leave it
    # out, and the contradiction would then be the validator's problem
    # instead of the caller's.
    outcome_class: ProviderOutcomeClass = Field(
        default=ProviderOutcomeClass.SUCCESS,
        description="Closed-vocabulary outcome for this call",
    )
    response_time_ms: float = Field(
        ge=0.0,
        description="Response time in milliseconds",
    )
    error_message: NotBlankStr | None = Field(
        default=None,
        max_length=1024,
        description="Error description when success is False",
    )
    source: RecordSource = Field(
        default=RecordSource.REAL_CALL,
        description="Whether a real call or a probe produced this record",
    )
    # Attribution is absent rather than invented when no cost scope is open.
    # An id naming no row would make an agent's history look complete while
    # pointing at nothing, which is worse than a gap that reads as one.
    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Agent attributed with the call, when one was in scope",
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Task attributed with the call, when one was in scope",
    )

    @model_validator(mode="before")
    @classmethod
    def _default_outcome_from_success(cls, data: object) -> object:
        """Fill an omitted ``outcome_class`` from the ``success`` flag.

        A caller that knows only whether the call worked (a probe, a
        connection test) gets a coherent record; one that classified the
        failure supplies the class and keeps the detail. ``OTHER`` is the
        honest unclassified failure: the window still counts it as a
        failure, and no bucket claims to know which.

        Returns:
            The input, with ``outcome_class`` supplied when it was absent.
        """
        if not isinstance(data, dict) or "outcome_class" in data:
            return data
        succeeded = data.get("success")
        if not isinstance(succeeded, bool):
            return data
        derived = (
            ProviderOutcomeClass.SUCCESS if succeeded else ProviderOutcomeClass.OTHER
        )
        return {**data, "outcome_class": derived}

    @model_validator(mode="after")
    def _validate_error_consistency(self) -> Self:
        """Ensure the outcome fields describe one consistent fact.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``success`` disagrees with ``error_message`` or
                with ``outcome_class``.
        """
        succeeded_class = self.outcome_class is ProviderOutcomeClass.SUCCESS
        if self.success and self.error_message is not None:
            msg = "error_message must be None when success is True"
            raise ValueError(msg)
        if not self.success and self.error_message is None:
            msg = "error_message must be provided when success is False"
            raise ValueError(msg)
        if self.success != succeeded_class:
            msg = (
                f"success={self.success} contradicts "
                f"outcome_class={self.outcome_class.value!r}"
            )
            raise ValueError(msg)
        return self


class CallOutcome(BaseModel):
    """What a caller observed about one finished call.

    Exactly the record minus the three things the caller does not know and
    the recorder does: which provider it is bound to, what time it is, and
    whether it is measuring real traffic or a probe. Travelling as one value
    keeps those halves from being mixed up at a call site, and keeps the
    recording signature from growing a parameter per fact.

    Attributes:
        success: Whether the call succeeded.
        response_time_ms: Round-trip time the caller measured.
        error_message: Redacted failure description, when it failed.
        model: Model the call named; ``None`` when it named none.
        outcome_class: Classified outcome; derived from ``success`` when the
            caller did not classify the failure.
        agent_id: Agent attributed with the call, when one was in scope.
        task_id: Task attributed with the call, when one was in scope.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    success: bool
    response_time_ms: float = Field(ge=0.0)
    error_message: str | None = Field(default=None, max_length=1024)
    model: str | None = Field(default=None)
    outcome_class: ProviderOutcomeClass | None = Field(default=None)
    agent_id: str | None = Field(default=None)
    task_id: str | None = Field(default=None)

    @property
    def resolved_outcome_class(self) -> ProviderOutcomeClass:
        """The classified outcome, derived from ``success`` when unset."""
        if self.outcome_class is not None:
            return self.outcome_class
        return (
            ProviderOutcomeClass.SUCCESS if self.success else ProviderOutcomeClass.OTHER
        )

    @model_validator(mode="after")
    def _validate_outcome_consistency(self) -> Self:
        """Reject an outcome the health record could not represent.

        The recorder copies this straight into a ``ProviderHealthRecord``,
        which enforces exactly these three rules. Without them here, a driver
        that catches an exception with empty text builds a coherent-looking
        ``CallOutcome`` and the contradiction surfaces as a ``ValidationError``
        on the provider call path instead of at the caller that built it.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``success`` disagrees with ``error_message`` or
                with ``outcome_class``.
        """
        resolved = self.resolved_outcome_class
        if self.success and self.error_message is not None:
            msg = "error_message must be None when success is True"
            raise ValueError(msg)
        if not self.success and self.error_message is None:
            msg = "error_message must be provided when success is False"
            raise ValueError(msg)
        if self.success != (resolved is ProviderOutcomeClass.SUCCESS):
            msg = f"success={self.success} contradicts outcome_class={resolved.value!r}"
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

    @model_validator(mode="after")
    def _validate_liveness_subset(self) -> Self:
        """Ensure the liveness sample is a subset of the 24h window.

        The liveness records are drawn from the same pruned window the 24h
        stats are computed over, so more liveness calls than total calls is
        not a stricter reading of the data: it is two fields describing
        different record sets, which is the exact confusion this pair of
        fields exists to end.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``liveness_calls`` exceeds ``calls_last_24h``, or
                a zero-sample liveness rate is non-zero.
        """
        if self.liveness_calls > self.calls_last_24h:
            msg = "liveness_calls cannot exceed calls_last_24h"
            raise ValueError(msg)
        if self.liveness_calls == 0 and self.liveness_error_rate_percent != 0.0:
            msg = "liveness_error_rate_percent must be 0 when liveness_calls is 0"
            raise ValueError(msg)
        return self

    @computed_field
    @property
    def health_status(self) -> ProviderHealthStatus:
        """Whether the provider is serving, on its newest outcomes alone.

        Deliberately blind to ``error_rate_percent_24h``. The two answer
        different questions on different timescales, and one number cannot
        answer both: a day of accumulated failures outweighs any number of
        recent successes, so a provider that has resumed serving could never
        clear a DOWN verdict while those failures stay in the window.
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
    records: Sequence[ProviderHealthRecord],
    *,
    liveness_records: Sequence[ProviderHealthRecord],
) -> ProviderHealthSummary:
    """Aggregate a non-empty list of health records into a summary.

    Args:
        records: Non-empty sequence of health records inside the 24h window
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
    "CallOutcome",
    "ProviderHealthRecord",
    "ProviderHealthStatus",
    "ProviderHealthSummary",
    "ProviderOutcomeClass",
    "ProviderReachability",
    "RecordSource",
    "aggregate_records",
    "worst_reachability",
]
