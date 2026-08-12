# module-kind: code
"""Turning an observed provider call outcome into a health record.

Measuring a provider and recording the verdict are separate jobs, and only
the first belongs to the prober. The reachability sweep is one source of
outcomes; a connection test is another, and it is the only one that reaches
a provider configured without a ``base_url``, which the sweep skips
outright. Both land here so a record is built exactly one way.

What a record carries beyond success and latency is decided here too. The
model, the outcome class and the run attribution are all already in scope
wherever a real call is made, and none of them can be recovered afterwards,
so they are threaded in rather than reconstructed: a provider-level verdict
cannot say which model is failing, and a boolean cannot say whether it is
queueing or refusing to bill. They travel as one :class:`CallOutcome`, which
is the record minus the three things only the recorder knows.

Deliberately silent: each caller already reports the outcome in its own
vocabulary (a probe result, a connection test), and logging here as well
would report every outcome twice under two different names.
"""

from collections.abc import Awaitable
from typing import Protocol

from synthorg.core.clock import Clock
from synthorg.providers.health import (
    CallOutcome,
    ProviderHealthRecord,
    RecordSource,
)
from synthorg.providers.health_tracker import ProviderHealthTracker


async def record_call_outcome(
    tracker: ProviderHealthTracker,
    name: str,
    outcome: CallOutcome,
    *,
    clock: Clock,
    source: RecordSource = RecordSource.REAL_CALL,
) -> float:
    """Record one observed call outcome against *name*'s health.

    Args:
        tracker: Sink the record is appended to.
        name: Provider the outcome belongs to.
        outcome: What the caller observed.
        clock: Time source the record is stamped from.
        source: Whether a real call or a probe produced this outcome.

    Returns:
        The rounded latency stored on the record, so a caller reporting the
        outcome quotes the number that was actually recorded.
    """
    latency = round(outcome.response_time_ms, 1)
    await tracker.record(
        ProviderHealthRecord(
            provider_name=name,
            model=outcome.model,
            timestamp=clock.now(),
            success=outcome.success,
            outcome_class=outcome.resolved_outcome_class,
            response_time_ms=latency,
            error_message=outcome.error_message,
            source=source,
            agent_id=outcome.agent_id,
            task_id=outcome.task_id,
        )
    )
    return latency


class CallOutcomeRecorder(Protocol):
    """Records one finished call against the health of a fixed provider.

    The provider is bound when the recorder is built, so a driver reports
    what happened without holding the tracker or knowing its own registry
    key. Injected rather than imported so the completion path stays free of
    the app state that owns the tracker.
    """

    # Positional-only: a one-argument callback should not force every
    # implementation to spell the parameter the same way.
    def __call__(self, outcome: CallOutcome, /) -> Awaitable[None]:
        """Record the outcome of one call against the bound provider."""
        ...


def outcome_recorder_for(
    tracker: ProviderHealthTracker,
    name: str,
    *,
    clock: Clock,
    source: RecordSource = RecordSource.REAL_CALL,
) -> CallOutcomeRecorder:
    """Bind a recorder that files *name*'s call outcomes into *tracker*.

    Args:
        tracker: Sink the records are appended to.
        name: Provider every outcome from this recorder belongs to.
        clock: Time source the records are stamped from.
        source: What this recorder's outcomes are evidence of. Bound here
            rather than passed per call, because a recorder is handed to one
            kind of caller and cannot change what it is measuring.

    Returns:
        A recorder the provider's own call path can report through.
    """

    async def _record(outcome: CallOutcome) -> None:
        _ = await record_call_outcome(
            tracker, name, outcome, clock=clock, source=source
        )

    return _record


__all__ = ["CallOutcomeRecorder", "outcome_recorder_for", "record_call_outcome"]
