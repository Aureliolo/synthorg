# module-kind: code
"""Security feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.security import SECURITY_POLICY_ENGINE_ERROR
from synthorg.security.policy_engine.config import build_policy_engine
from synthorg.security.policy_engine.protocol import PolicyEngine
from synthorg.security.state import SecurityStateSlice

logger = get_logger(__name__)

if TYPE_CHECKING:
    # Genuine cycle: ``api.construction_wiring`` imports this security slice
    # (and ``security.audit`` / ``autonomy.protocol`` / ``trust.service``)
    # directly, and ``api.state`` transitively pulls ``security``; a module-level
    # import of either here closes that loop. ``wire_construction`` runs only at
    # app construction (the blessed back-edge), never in a security unit test, so
    # this guard is not reached under the typeguard ERROR policy.
    from synthorg.api.construction_wiring import ConstructionDeps
    from synthorg.api.state import AppState


def _build_policy_engine_or_none(deps: ConstructionDeps) -> PolicyEngine | None:
    """Build the runtime policy engine from config (``none`` by default).

    With ``engine='none'`` the result is ``None`` and the tool-invoker
    policy seam is a transparent pass-through. When an engine IS configured
    (``engine != 'none'``) and the build fails, this FAILS HARD (re-raises)
    rather than degrading to ``None``: an operator who configured Cedar must
    not silently get zero enforcement because a policy file was momentarily
    unreadable at boot.

    Returns:
        The configured policy engine, or ``None`` when ``engine='none'``.

    Raises:
        Exception: When a non-``none`` engine fails to build (fail-closed).
    """
    config = deps.effective_config.security.policy_engine
    try:
        return build_policy_engine(config)
    except Exception as exc:
        reraise_critical(exc)
        logger.error(
            SECURITY_POLICY_ENGINE_ERROR,
            context="policy_engine_build_failed",
            engine=config.engine,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        if config.engine != "none":
            raise
        return None


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the security slice (audit log, trust, autonomy, policy)."""
    app_state.swap_slice(
        SecurityStateSlice.model_construct(
            audit_log=deps.audit_log,
            trust_service=deps.trust_service,
            autonomy_change_strategy=deps.autonomy_change_strategy,
            policy_engine=_build_policy_engine_or_none(deps),
        )
    )
