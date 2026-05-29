# module-kind: code
"""Notifications feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.notifications.state import NotificationsStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the notifications slice (dispatcher)."""
    app_state.swap_slice(
        NotificationsStateSlice.model_construct(
            dispatcher=deps.notification_dispatcher,
        )
    )
