"""Demo feature construction-phase state-slice wiring."""

from synthorg._demo.service import DemoService
from synthorg._demo.state import DemoStateSlice
from synthorg.api.construction_wiring import ConstructionDeps
from synthorg.api.state import AppState
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Build the demo service and populate the demo slice.

    The greeting is sourced from the ``demo.greeting`` setting (env > code
    default) via the bootstrap resolver, proving a feature wires its own
    service from its own directory with no central edits.

    Args:
        app_state: Application state container to swap the slice into.
        deps: Construction-time dependency bundle (unused by the demo).
    """
    del deps
    greeting = str(resolve_init_value(SettingNamespace.DEMO, "greeting").value)
    app_state.swap_slice(
        DemoStateSlice.model_construct(service=DemoService(greeting=greeting))
    )
