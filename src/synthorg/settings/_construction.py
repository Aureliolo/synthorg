# module-kind: code
"""Settings feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.settings.state import SettingsStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the settings slice (settings service)."""
    app_state.swap_slice(
        SettingsStateSlice.model_construct(settings_service=deps.settings_service)
    )
