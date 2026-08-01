# module-kind: code
"""The single entry point boot and every trigger both call.

There is deliberately one function here. Boot is not a special path: it is
the first reconcile, and a settings write, a provider mutation or the
periodic resync are the same call with a different label.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.capabilities import CAPABILITIES
from synthorg.api.subsystems.reconciler import ReconcileReport, SubsystemReconciler
from synthorg.api.subsystems.registry import SUBSYSTEMS
from synthorg.api.subsystems.state import SubsystemsStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.subsystem import SUBSYSTEM_RECONCILE_FAILED

logger = get_logger(__name__)


def reconciler_of(app_state: AppState) -> SubsystemReconciler:
    """Return the reconciler, building it on first use.

    Args:
        app_state: Application state holding the reconciler slice.

    Returns:
        The reconciler for this application.

    Raises:
        SubsystemGraphInvalidError: When the declarations cannot be ordered.
    """
    existing = app_state.slice(SubsystemsStateSlice).reconciler
    if existing is not None:
        return existing
    built = SubsystemReconciler(SUBSYSTEMS, CAPABILITIES)
    app_state.wire(SubsystemsStateSlice, reconciler=built)
    return built


async def reconcile_subsystems(
    app_state: AppState,
    *,
    trigger: str,
) -> ReconcileReport | None:
    """Drive every declared subsystem toward its desired state.

    Safe to call from any trigger at any time. A pass over an already
    converged system does nothing, so a caller never needs to know whether
    the change it just made is relevant to any particular subsystem.

    Args:
        app_state: Application state the checks and wiring read.
        trigger: What prompted this pass, for the logs only.

    Returns:
        The pass report, or ``None`` when the pass itself could not run.
    """
    try:
        return await reconciler_of(app_state).reconcile(app_state, trigger=trigger)
    except Exception as exc:  # noqa: BLE001 -- a trigger must not take the caller down
        reraise_critical(exc)
        # Reaching here means the pass could not run at all, not that a
        # subsystem failed: the reconciler records those and carries on. A
        # settings write or a provider edit must still succeed, and the next
        # pass retries from whatever state this one left.
        logger.error(
            SUBSYSTEM_RECONCILE_FAILED,
            trigger=trigger,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
