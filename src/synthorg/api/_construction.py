# module-kind: code
"""API-core feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.auth.presence import UserPresence
from synthorg.api.auth.ticket_store import WsTicketStore

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the api-core slice (cursor secret, auth, ws ticket, presence)."""
    app_state.swap_slice(
        ApiCoreStateSlice.model_construct(
            cursor_secret=deps.cursor_secret,
            auth_service=deps.auth_service,
            ticket_store=WsTicketStore(),
            user_presence=UserPresence(),
        )
    )
