# module-kind: code
"""Late-bound dependencies pushed onto every registered driver.

Boot builds the provider registry before the services a driver reports to
exist: the credential catalog and the health tracker both need a connected
persistence backend. Whoever holds them later binds them here, over the
registry's public surface, so the registry itself stays a lookup table
rather than growing a wiring phase.

Every rebuild of the provider set needs the same bindings re-applied, and
three separate paths rebuild it, so they are applied by one function rather
than assembled call by call. Two of the three had already come to bind the
health recorders and not the billing snapshot, which left an operator's
correction to how a connection charges reaching the ledger only on the next
restart.
"""

from collections.abc import Mapping

from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.config.provider_schema import ProviderConfig
from synthorg.core.clock import Clock
from synthorg.providers.health_recording import outcome_recorder_for
from synthorg.providers.health_tracker import ProviderHealthTracker
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
    *,
    clock: Clock,
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
        clock: The app's clock, not a fresh one. A recheck marks its liveness
            cutoff on that clock and then compares recorded outcomes against
            it, so a second time source here can date an outcome before the
            cutoff it actually followed.
    """
    from synthorg.providers.state import ProvidersStateSlice  # noqa: PLC0415

    slice_ = app_state.slice(ProvidersStateSlice)
    bind_health_recorders(registry, slice_.health_tracker, clock=clock)


def rebind_provider_set(
    app_state: AppStateSliceMixin,
    registry: ProviderRegistry,
    provider_configs: Mapping[str, ProviderConfig],
    *,
    clock: Clock,
) -> None:
    """Re-apply every binding a freshly built provider set needs.

    The one call each rebuild path makes, so a path cannot acquire the
    health recorders and miss the billing snapshot: both describe the same
    provider set, and a registry published with one of them bound is a
    registry that reports its outcomes or stamps its ledger rows against
    the set it replaced.

    Call it before publishing the registry, so it is never reachable in a
    partly-bound state.

    Args:
        app_state: Application state holding the health tracker and the
            cost tracker the bindings are pushed into.
        registry: The registry about to be published.
        provider_configs: The provider set that registry was built from.
        clock: The app's clock, forwarded to the health recorders.
    """
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.providers.billing_model_snapshot import (  # noqa: PLC0415
        ProviderBillingModelSnapshot,
    )

    rebind_health_recorders(app_state, registry, clock=clock)
    # A trackerless harness records nothing to stamp.
    tracker = app_state.slice(BudgetStateSlice).cost_tracker
    if tracker is not None:
        tracker.bind_billing_model_resolver(
            ProviderBillingModelSnapshot(provider_configs)
        )


__all__ = [
    "bind_health_recorders",
    "rebind_health_recorders",
    "rebind_provider_set",
]
