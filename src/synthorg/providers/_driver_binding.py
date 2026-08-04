# module-kind: code
"""Late-bound dependencies pushed onto every registered driver.

Boot builds the provider registry before the services a driver reports to
exist: the credential catalog and the health tracker both need a connected
persistence backend. Whoever holds them later binds them here, over the
registry's public surface, so the registry itself stays a lookup table
rather than growing a wiring phase.
"""

from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.core.clock import Clock, SystemClock
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


def rebind_health_recorders(
    app_state: AppStateSliceMixin,
    registry: ProviderRegistry,
) -> None:
    """Re-point a freshly built *registry* at the app's live health tracker.

    A registry is rebuilt from scratch on every provider mutation and on the
    persisted-config reload, and its drivers come back unbound. Publishing
    one without this leaves every completion reporting nowhere, so the 24h
    error rate quietly reverts to describing only the reachability sweep's
    pings from the first provider edit onward. Boot binds the first registry;
    this binds every one after it.

    Args:
        app_state: Application state holding the health tracker.
        registry: The registry about to be, or just, published.
    """
    from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415

    slice_ = app_state.slice(ProvidersStateSlice)
    bind_health_recorders(registry, slice_.health_tracker, clock=SystemClock())


__all__ = ["bind_health_recorders", "rebind_health_recorders"]
