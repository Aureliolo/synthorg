# module-kind: code
"""Coordination feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.coordination.service import CoordinationService
from synthorg.coordination.state import CoordinationStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the coordination slice (metrics store + read facade).

    The coordination read facade projects the metrics store, so it wires
    only when that store is present.
    """
    metrics_store = deps.coordination_metrics_store
    app_state.swap_slice(
        CoordinationStateSlice.model_construct(
            metrics_store=metrics_store,
            coordination_service=(
                CoordinationService(metrics_store=metrics_store)
                if metrics_store is not None
                else None
            ),
        )
    )
