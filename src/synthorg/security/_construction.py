# module-kind: code
"""Security feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.security.state import SecurityStateSlice

if TYPE_CHECKING:
    # Genuine cycle: ``api.construction_wiring`` imports this security slice
    # (and ``security.audit`` / ``autonomy.protocol`` / ``trust.service``)
    # directly, and ``api.state`` transitively pulls ``security``; a module-level
    # import of either here closes that loop. ``wire_construction`` runs only at
    # app construction (the blessed back-edge), never in a security unit test, so
    # this guard is not reached under the typeguard ERROR policy.
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
