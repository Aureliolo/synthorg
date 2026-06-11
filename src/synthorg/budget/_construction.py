# module-kind: code
"""Budget feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.budget.state import BudgetStateSlice

if TYPE_CHECKING:
    # Cycle breaker: ``api.construction_wiring`` pulls the
    # ``communication.config`` engine<->communication cold-import cycle, so
    # ``ConstructionDeps`` is named for signatures only.
    from synthorg.api.construction_wiring import ConstructionDeps


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the budget slice (cost tracker)."""
    app_state.swap_slice(
        BudgetStateSlice.model_construct(cost_tracker=deps.phase1.cost_tracker)
    )
