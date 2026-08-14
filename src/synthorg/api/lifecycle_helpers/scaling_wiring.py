# module-kind: orchestrator
"""On-startup wiring for the HR dynamic-scaling pipeline.

The ``ScalingService`` orchestrates workload / budget / skill / performance
signals into hire and prune decisions. This hook assembles it over the already
published hiring pipeline (see ``hiring_wiring``) plus an
:class:`OffboardingService`, and publishes it on :class:`HrStateSlice` so the
``/scaling`` endpoints come alive.

Running a scaling evaluation triggers real auto-hire / auto-prune decisions, so
it is OPT-IN behind ``hr.scaling_enabled`` (off by default). The service is
ghost-wired: always constructed when its collaborators exist, and the switch is
enforced live at the ``/scaling/evaluate`` entrypoint, so toggling it takes
effect on the next request with no restart. Best-effort + idempotent +
collaborator-gated: an already-wired service short-circuits, and a missing
collaborator (no registry / tracker / approval store / hiring pipeline) leaves
the service absent rather than poisoning startup.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_scaling(app_state: AppState) -> None:
    """Construct the scaling + hiring pipeline at boot when collaborators exist.

    Ghost-wired: the pipeline is built whenever its collaborators are present,
    regardless of ``hr.scaling_enabled``. The switch is enforced live at the
    ``/scaling/evaluate`` entrypoint, so toggling it takes effect on the next
    request with no restart.

    Args:
        app_state: The application state holding the collaborator slices.

    Raises:
        SubsystemDeclinedError: A collaborator the pipeline is assembled
            from is absent, named so the status surface can report which.
    """
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415

    hr = app_state.slice(HrStateSlice)
    if hr.scaling_service is not None:
        return
    # Ghost-wired: the service is always constructed when its collaborators
    # exist, regardless of ``hr.scaling_enabled``. The switch is enforced live
    # at the ``/scaling/evaluate`` entrypoint, so toggling it takes effect on
    # the next request with no restart. A missing collaborator is routine
    # (the service simply stays absent and the endpoint 503s), so it logs INFO.
    approval_store = app_state.slice(ApprovalStateSlice).store
    if hr.agent_registry is None:
        msg = "no agent registry; scaling adds to the roster"
        raise SubsystemDeclinedError(msg)
    if hr.performance_tracker is None:
        msg = "no performance tracker; scaling is decided from its records"
        raise SubsystemDeclinedError(msg)
    if approval_store is None:
        msg = "no approval store; hiring is a gated decision"
        raise SubsystemDeclinedError(msg)
    if hr.hiring_service is None:
        msg = "no hiring pipeline; a scale-up decision has to be able to hire"
        raise SubsystemDeclinedError(msg)
    try:
        await _wire(
            app_state,
            registry=hr.agent_registry,
            tracker=hr.performance_tracker,
            approval_store=approval_store,
            hiring=hr.hiring_service,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="scaling",
            note="scaling wiring failed; service stays unwired",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire(
    app_state: AppState,
    *,
    registry: AgentRegistryService,
    tracker: PerformanceTracker,
    approval_store: ApprovalStoreProtocol,
    hiring: HiringService,
) -> None:
    from synthorg.hr.offboarding_service import OffboardingService  # noqa: PLC0415
    from synthorg.hr.scaling.config import ScalingConfig  # noqa: PLC0415
    from synthorg.hr.scaling.decision_service import (  # noqa: PLC0415
        ScalingDecisionService,
    )
    from synthorg.hr.scaling.factory import build_scaling_service  # noqa: PLC0415
    from synthorg.memory.state import org_memory_backend_of  # noqa: PLC0415

    # ``wire_org_memory_backend`` runs with the memory backend, earlier than
    # this hook, so the org-memory backend is published by now; thread it in
    # so an offboarding snapshot persists the departing agent's facts
    # instead of dropping them.
    offboarding = OffboardingService(
        registry=registry,
        org_memory_backend=org_memory_backend_of(app_state),
        performance_tracker=tracker,
    )

    config = ScalingConfig()
    service = build_scaling_service(
        config,
        hiring_service=hiring,
        offboarding_service=offboarding,
        agent_registry=registry,
        approval_store=approval_store,
    )
    # The MCP scaling-decision facade reads the same service's recent-decision
    # history and manual-trigger path, so it wires from the just-built service.
    app_state.wire(
        HrStateSlice,
        scaling_service=service,
        scaling_decision_service=ScalingDecisionService(scaling=service),
    )
    logger.info(API_APP_STARTUP, service="scaling", note="wired (durable)")


__all__ = ["wire_scaling"]
