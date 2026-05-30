# module-kind: code
"""Persistence feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.persistence.state import PersistenceStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the persistence slice (backend)."""
    app_state.swap_slice(
        PersistenceStateSlice.model_construct(backend=deps.persistence)
    )
