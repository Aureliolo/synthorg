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
    """Populate the HR slice (agent registry, performance, training, health).

    The agent-health read facade derives its verdict purely from the
    construction-injected performance tracker, so it wires here whenever the
    tracker is present rather than 503-ing the synthorg_agents_get_health tool.
    """
    from synthorg.hr.health.service import AgentHealthService  # noqa: PLC0415

    performance_tracker = deps.performance_tracker
    app_state.swap_slice(
        HrStateSlice.model_construct(
            agent_registry=deps.agent_registry,
            performance_tracker=performance_tracker,
            agent_health_service=(
                AgentHealthService(performance_tracker=performance_tracker)
                if performance_tracker is not None
                else None
            ),
        )
    )
