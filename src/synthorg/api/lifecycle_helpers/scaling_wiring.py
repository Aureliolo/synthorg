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

Activating the pipeline starts real auto-hire / auto-prune evaluation, so it
is OPT-IN behind ``hr.scaling_enabled`` (off by default, baked at startup like
``eval_loop_cycle_enabled``). Best-effort + idempotent + persistence-gated: an
already-wired service short-circuits, the disabled gate leaves the service
absent, and a missing collaborator (no persistence / registry / tracker /
approval store) leaves the service absent rather than poisoning startup.
"""

from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.settings.bootstrap_resolver import resolve_init_value
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import parse_bool

if TYPE_CHECKING:
    # Annotation-only imports: kept out of the module body so this early
    # boot-wiring helper does not pull the HR / approval hubs into the
    # cold-import graph (the concrete classes are imported lazily in _wire).
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.hr.registry import AgentRegistryService

logger = get_logger(__name__)


async def wire_scaling(app_state: AppState) -> None:
    """Construct the scaling + hiring pipeline at boot when opted in.

    Args:
        app_state: The application state holding the collaborator slices.
    """
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    hr = app_state.slice(HrStateSlice)
    if hr.scaling_service is not None:
        return
    enabled = bool(
        resolve_init_value(
            SettingNamespace.HR,
            "scaling_enabled",
            parse=parse_bool,
        ).value
    )
    if not enabled:
        logger.info(
            API_APP_STARTUP,
            service="scaling",
            note="disabled (opt-in hr.scaling_enabled); pipeline unwired",
        )
        return
    if app_state.slice(PersistenceStateSlice).backend is None:
        logger.info(
            API_APP_STARTUP,
            service="scaling",
            note="persistence absent; scaling service unwired",
        )
        return
    approval_store = app_state.slice(ApprovalStateSlice).store
    if (
        hr.agent_registry is None
        or hr.performance_tracker is None
        or approval_store is None
    ):
        logger.info(
            API_APP_STARTUP,
            service="scaling",
            note="registry / tracker / approval store absent; scaling unwired",
        )
        return
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
    from synthorg.hr.scaling.factory import build_scaling_service  # noqa: PLC0415
    from synthorg.memory.state import org_memory_backend_of  # noqa: PLC0415
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    # ``persistence_of(app_state).hiring_requests`` is the per-backend durable
    # repo shipped in the durability PR; attach + hydrate the in-flight set so
    # an approved request survives a restart between approval and instantiation.
    hiring = HiringService(registry=registry, approval_store=approval_store)
    hiring.attach_persistence(request_repo=persistence_of(app_state).hiring_requests)
    await hiring.hydrate()

    # ``_wire_org_memory_backend`` runs earlier in ``_wire_features``, so the
    # org-memory backend is published by now; thread it in so an offboarding
    # snapshot persists the departing agent's facts instead of dropping them.
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
    app_state.wire(HrStateSlice, scaling_service=service)
    logger.info(API_APP_STARTUP, service="scaling", note="wired (durable)")


__all__ = ["wire_scaling"]
