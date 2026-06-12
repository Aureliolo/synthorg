# module-kind: code
"""Providers feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.providers.state import ProvidersStateSlice

if TYPE_CHECKING:
    # Cycle breaker: synthorg.api.construction_wiring aggregates every
    # feature's construction wiring (providers included), so a module-level
    # import closes an api.construction_wiring -> providers._construction
    # cycle; ConstructionDeps is named for the signature only.
    from synthorg.api.construction_wiring import ConstructionDeps


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the providers slice (registry + health tracker)."""
    app_state.swap_slice(
        ProvidersStateSlice.model_construct(
            registry=deps.phase1.provider_registry,
            health_tracker=deps.phase1.provider_health_tracker,
        )
    )
