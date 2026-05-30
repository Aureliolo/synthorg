# module-kind: code
"""Providers feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.providers.state import ProvidersStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the providers slice (registry + health tracker)."""
    app_state.swap_slice(
        ProvidersStateSlice.model_construct(
            registry=deps.phase1.provider_registry,
            health_tracker=deps.phase1.provider_health_tracker,
        )
    )
