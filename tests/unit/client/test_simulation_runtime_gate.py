"""``client_simulation_state_of`` 503 gate.

The simulation/request controllers mount unconditionally, so an absent or
partially-wired runtime must surface a clean ``ServiceUnavailableError``
(503) rather than a 404 or an ``AttributeError`` on a ``None`` engine.
"""

import pytest

from synthorg.client.simulation_state import ClientSimulationState
from synthorg.client.state import (
    ClientStateSlice,
    client_simulation_state_of,
    has_simulation_runtime,
)
from synthorg.core.domain_errors import ServiceUnavailableError
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


def test_503_when_slice_absent() -> None:
    app_state = make_app_state()
    app_state.set_slice(ClientStateSlice(simulation_state=None))
    assert has_simulation_runtime(app_state) is False
    with pytest.raises(ServiceUnavailableError):
        client_simulation_state_of(app_state)


def test_503_when_runtime_partially_wired() -> None:
    # Slice present but no intake engine / review pipeline (no provider).
    app_state = make_app_state()
    app_state.set_slice(ClientStateSlice(simulation_state=ClientSimulationState()))
    assert has_simulation_runtime(app_state) is False
    with pytest.raises(ServiceUnavailableError):
        client_simulation_state_of(app_state)
