# module-kind: code
"""Coordination feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.coordination.state import CoordinationStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the coordination slice (metrics store)."""
    app_state.swap_slice(
        CoordinationStateSlice.model_construct(
            metrics_store=deps.coordination_metrics_store,
        )
    )
