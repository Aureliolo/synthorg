"""Client feature state slice.

Holds the in-memory client-simulation state (the demo / dry-run
client world). ``None`` until wired; the simulation controllers raise
503 on a ``None`` value.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.client.simulation_state import ClientSimulationState

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class ClientStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the client feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    simulation_state: ClientSimulationState | None = None


def has_simulation_runtime(app_state: AppStateSliceMixin) -> bool:
    """Report whether the full simulation runtime is wired.

    Requires both ``intake_engine`` and ``review_pipeline`` on the
    attached ``ClientSimulationState``; without them the simulation and
    request controllers cannot execute end-to-end flows, so the
    optional-controller predicate keeps their routes off the router and
    the ``/capabilities`` flag reads ``False`` so the dashboard skips
    polling them.

    Returns:
        ``True`` only when both the intake engine and review pipeline
        are wired on the simulation state.
    """
    simulation = app_state.slice(ClientStateSlice).simulation_state
    if simulation is None:
        return False
    return (
        simulation.intake_engine is not None and simulation.review_pipeline is not None
    )


def client_simulation_state_of(
    app_state: AppStateSliceMixin,
) -> ClientSimulationState:
    """Resolve the fully-wired client simulation state, or raise 503.

    The simulation/request controllers mount unconditionally, so this
    guard is the single place that turns an absent or partially-wired
    runtime into a clean ``ServiceUnavailableError`` (503) instead of a
    404 (route absent) or an ``AttributeError`` on a ``None`` engine. A
    runtime that lacks an LLM provider / task engine leaves
    ``intake_engine`` / ``review_pipeline`` unwired; both are required to
    run an end-to-end simulation flow.

    Returns:
        The wired client simulation state with both the intake engine and
        review pipeline present.
    """
    state = require_service(
        app_state.slice(ClientStateSlice).simulation_state,
        "Client Simulation State",
    )
    require_service(state.intake_engine, "Client Simulation Intake Engine")
    require_service(state.review_pipeline, "Client Simulation Review Pipeline")
    return state
