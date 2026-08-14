# module-kind: orchestrator
"""On-startup wiring for the HR dynamic-scaling pipeline.

The ``ScalingService`` orchestrates workload / budget / skill / performance
signals into hire and prune decisions, and the durable ``hiring_requests``
repository plus ``HiringService.attach_persistence`` / ``hydrate`` seam ship
ready but dead until the pipeline is constructed at boot. This hook builds
the :class:`HiringService` over the durable repo (rehydrating in-flight
requests so an approved hire is not orphaned by a restart), an
:class:`OffboardingService`, and the :class:`ScalingService`, then publishes
the service on :class:`HrStateSlice` so the ``/scaling`` endpoints come alive.

Running a scaling evaluation triggers real auto-hire / auto-prune decisions, so
it is OPT-IN behind ``hr.scaling_enabled`` (off by default). The service is
ghost-wired: always constructed when its collaborators exist, and the switch is
enforced live at the ``/scaling/evaluate`` entrypoint, so toggling it takes
effect on the next request with no restart. Best-effort + idempotent +
collaborator-gated: an already-wired service short-circuits, and a missing
collaborator (no persistence / registry / tracker / approval store) leaves the
service absent rather than poisoning startup.
"""

from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    # Annotation-only imports: kept out of the module body so this early
    # boot-wiring helper does not pull the HR / approval hubs into the
    # cold-import graph (the concrete classes are imported lazily in _wire).
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.hr.registry import AgentRegistryService

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
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    hr = app_state.slice(HrStateSlice)
    if hr.scaling_service is not None:
        return
    # Ghost-wired: the service is always constructed when its collaborators
    # exist, regardless of ``hr.scaling_enabled``. The switch is enforced live
    # at the ``/scaling/evaluate`` entrypoint, so toggling it takes effect on
    # the next request with no restart. A missing collaborator is routine
    # (the service simply stays absent and the endpoint 503s), so it logs INFO.
    if app_state.slice(PersistenceStateSlice).backend is None:
        msg = "no persistence backend; hiring requests are durable"
        raise SubsystemDeclinedError(msg)
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
    try:
        await _wire(
            app_state,
            registry=hr.agent_registry,
            tracker=hr.performance_tracker,
            approval_store=approval_store,
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
) -> None:
    from synthorg.hr.hiring_service import HiringService  # noqa: PLC0415
    from synthorg.hr.offboarding_service import OffboardingService  # noqa: PLC0415
    from synthorg.hr.scaling.config import ScalingConfig  # noqa: PLC0415
    from synthorg.hr.scaling.decision_service import (  # noqa: PLC0415
        ScalingDecisionService,
    )
    from synthorg.hr.scaling.factory import build_scaling_service  # noqa: PLC0415
    from synthorg.memory.state import org_memory_backend_of  # noqa: PLC0415
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    # Attach the per-backend durable hiring-requests repo, then hydrate the
    # in-flight set so an approved request survives a restart between approval
    # and instantiation. Attachment is a hard prerequisite: a failure there
    # would leave the service non-durable, so it stays outside the guard and
    # aborts wiring through the outer handler. Only hydration is isolated -- a
    # failure there merely leaves in-flight requests unrestored (orphaned), so
    # it is logged on its own and wiring still proceeds, bringing the pipeline
    # up degraded (no recovered in-flight requests) rather than dormant.
    hiring = HiringService(
        registry=registry,
        approval_store=approval_store,
        # The resolver, not a resolved pair: the hire reads it per
        # instantiation, so an operator who binds ``hr.new_hire_model``
        # after boot can approve a hire without a restart.
        config_resolver=config_resolver_of(app_state),
    )
    hiring.attach_persistence(request_repo=persistence_of(app_state).hiring_requests)
    try:
        await hiring.hydrate()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="scaling",
            note="hiring request hydration failed; in-flight requests may be orphaned",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )

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
        # Published in its own right, not only inside the scaler: an approved
        # ORG_HIRE decision arrives at the approvals controller, which has no
        # business reaching through the scaling service to find the pipeline
        # that must now actually register the agent.
        hiring_service=hiring,
        scaling_service=service,
        scaling_decision_service=ScalingDecisionService(scaling=service),
    )
    logger.info(API_APP_STARTUP, service="scaling", note="wired (durable)")


__all__ = ["wire_scaling"]
