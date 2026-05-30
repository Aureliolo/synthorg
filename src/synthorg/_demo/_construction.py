"""Demo feature construction-phase state-slice wiring."""

from synthorg._demo.service import DemoService
from synthorg._demo.state import DemoStateSlice
from synthorg.api.construction_wiring import ConstructionDeps
from synthorg.api.state import AppState

_DEMO_GREETING = "hello from the demo feature"


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Build the demo service and populate the demo slice.

    The greeting is a fixed boot constant; the demo feature has no runtime
    dependencies, proving a feature wires its own service from its own
    directory with no central edits.

    Args:
        app_state: Application state container to swap the slice into.
        deps: Construction-time dependency bundle (unused by the demo).
    """
    del deps
    app_state.swap_slice(
        DemoStateSlice.model_construct(service=DemoService(greeting=_DEMO_GREETING))
    )
