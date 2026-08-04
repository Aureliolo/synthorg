# module-kind: code
"""Late-bound dependencies pushed onto every registered driver.

Boot builds the provider registry before the services a driver reports to
exist: the credential catalog and the health tracker both need a connected
persistence backend. Whoever holds them later binds them here, over the
registry's public surface, so the registry itself stays a lookup table
rather than growing a wiring phase.
"""

from synthorg.core.clock import Clock
from synthorg.providers.health import ProviderHealthTracker
from synthorg.providers.health_recording import outcome_recorder_for
from synthorg.providers.registry import ProviderRegistry


def bind_health_recorders(
    registry: ProviderRegistry,
    tracker: ProviderHealthTracker | None,
    *,
    clock: Clock,
) -> None:
    """Point every registered driver's call outcomes at *tracker*.

    Real completion traffic is the broadest evidence of whether a provider is
    serving, and until it reaches the tracker the 24h error rate describes
    only the reachability sweep's own pings. Bound per driver under its
    registry name, so an outcome is filed against the provider the operator
    configured rather than whatever label the driver happens to expose.
    Idempotent.

    Args:
        registry: Registry whose drivers report their outcomes.
        tracker: Sink for call outcomes, or ``None`` to unbind (leaving every
            driver reporting nowhere, which is the trackerless harness path).
        clock: Time source the records are stamped from.
    """
    for name in registry.list_providers():
        registry.get(name).bind_health_recorder(
            outcome_recorder_for(tracker, name, clock=clock)
            if tracker is not None
            else None
        )


__all__ = ["bind_health_recorders"]
