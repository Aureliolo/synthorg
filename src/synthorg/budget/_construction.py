# module-kind: code
"""Budget feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.budget.state import BudgetStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the budget slice (cost tracker)."""
    app_state.swap_slice(
        BudgetStateSlice.model_construct(cost_tracker=deps.phase1.cost_tracker)
    )
