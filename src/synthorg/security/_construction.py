# module-kind: code
"""Security feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.security.state import SecurityStateSlice

if TYPE_CHECKING:
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the security slice (audit log, trust, autonomy strategy)."""
    app_state.swap_slice(
        SecurityStateSlice.model_construct(
            audit_log=deps.audit_log,
            trust_service=deps.trust_service,
            autonomy_change_strategy=deps.autonomy_change_strategy,
        )
    )
