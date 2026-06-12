# module-kind: code
"""HR feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.hr.state import HrStateSlice

if TYPE_CHECKING:
    # Cycle breaker: ``api.construction_wiring`` pulls the
    # ``communication.config`` engine<->communication cold-import cycle, so
    # ``ConstructionDeps`` is named for signatures only.
    from synthorg.api.construction_wiring import ConstructionDeps


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the HR slice (agent registry, performance, training)."""
    app_state.swap_slice(
        HrStateSlice.model_construct(
            agent_registry=deps.agent_registry,
            performance_tracker=deps.performance_tracker,
            training_service=deps.training_service,
        )
    )
