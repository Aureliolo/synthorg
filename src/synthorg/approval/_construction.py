# module-kind: code
"""Approval feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.approval.state import ApprovalStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the approval slice (approval store)."""
    app_state.swap_slice(ApprovalStateSlice.model_construct(store=deps.approval_store))
