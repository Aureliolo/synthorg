# module-kind: code
"""Workspace feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.engine.workspace.state import WorkspaceStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the workspace slice (artifact storage)."""
    app_state.swap_slice(
        WorkspaceStateSlice.model_construct(artifact_storage=deps.artifact_storage)
    )
