# module-kind: code
"""Client feature construction-phase state-slice wiring.

Wires the client-simulation runtime when a task engine is present (so the
``/simulations`` + ``/requests`` controller predicates register), otherwise an
empty fallback so the always-mounted ``ClientController`` serves an empty list
rather than 503-ing. ``build_client_simulation_runtime`` reads the providers,
engine, and budget slices, so this feature ``depends_on`` those three.
"""

from typing import TYPE_CHECKING

from synthorg.client.simulation_state import ClientSimulationState
from synthorg.client.state import ClientStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Wire the client-simulation runtime (or empty fallback) into the slice."""
    simulation_state = deps.client_simulation_state
    if simulation_state is None:
        if deps.phase1.task_engine is not None:
            from synthorg.client.runtime_builder import (  # noqa: PLC0415
                build_client_simulation_runtime,
            )

            simulation_state = build_client_simulation_runtime(app_state)
        else:
            simulation_state = ClientSimulationState()
    app_state.wire(ClientStateSlice, simulation_state=simulation_state)
