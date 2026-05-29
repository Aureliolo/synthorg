# module-kind: code
"""Tools feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.tools.state import ToolsStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the tools slice (invocation tracker)."""
    app_state.swap_slice(
        ToolsStateSlice.model_construct(
            invocation_tracker=deps.tool_invocation_tracker,
        )
    )
