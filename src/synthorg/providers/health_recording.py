# module-kind: code
"""Turning an observed provider call outcome into a health record.

Measuring a provider and recording the verdict are separate jobs, and only
the first belongs to the prober. The reachability sweep is one source of
outcomes; a connection test is another, and it is the only one that reaches
a provider configured without a ``base_url``, which the sweep skips
outright. Both land here so a record is built exactly one way.

Deliberately silent: each caller already reports the outcome in its own
vocabulary (a probe result, a connection test), and logging here as well
would report every outcome twice under two different names.
"""

from synthorg.core.clock import Clock
from synthorg.providers.health import ProviderHealthRecord, ProviderHealthTracker


async def record_call_outcome(
    tracker: ProviderHealthTracker,
    name: str,
    *,
    clock: Clock,
    success: bool,
    response_time_ms: float,
    error_message: str | None = None,
) -> float:
    """Record one observed call outcome against *name*'s health.

    Args:
        tracker: Sink the record is appended to.
        name: Provider the outcome belongs to.
        clock: Time source the record is stamped from.
        success: Whether the call succeeded.
        response_time_ms: Round-trip time the caller measured.
        error_message: Redacted failure description, when it failed.

    Returns:
        The rounded latency stored on the record, so a caller reporting the
        outcome quotes the number that was actually recorded.
    """
    latency = round(response_time_ms, 1)
    await tracker.record(
        ProviderHealthRecord(
            provider_name=name,
            timestamp=clock.now(),
            success=success,
            response_time_ms=latency,
            error_message=error_message,
        )
    )
    return latency


__all__ = ["record_call_outcome"]
