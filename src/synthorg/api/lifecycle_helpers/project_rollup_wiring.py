# module-kind: orchestrator
"""Startup wiring for the initiative rollup service.

Constructs the :class:`ProjectRollupService` once the task engine and
persistence exist, and registers it as a :class:`TaskEngine` observer so a
task reaching a terminal status advances the plan and project behind it.
Best-effort and idempotent: a boot missing either dependency leaves the
service unwired, and re-running after setup brings it online with no restart.
A re-run after a successful wire is guarded by the state slice, so the observer
is not registered twice.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.initiative.ports import RetroCapturePort
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_project_rollup_service(app_state: AppState) -> None:
    """Build + wire the initiative rollup service when its deps are present.

    Raises:
        MemoryError: Propagated from construction; interpreter-level
            criticals are never swallowed by the best-effort handler.
        RecursionError: Propagated from construction for the same reason.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    if app_state.slice(EngineStateSlice).project_rollup_service is not None:
        # Already wired: never re-register the observer (register_observer
        # appends unconditionally, so a re-run would double-fire it).
        return

    persistence = app_state.slice(PersistenceStateSlice).backend
    task_engine = app_state.slice(EngineStateSlice).task_engine
    if persistence is None or task_engine is None:
        logger.info(
            API_APP_STARTUP,
            service="project_rollup_service",
            note="rollup not wired; dependencies not yet present",
        )
        return
    try:
        from synthorg.api.services.plan_service import PlanService  # noqa: PLC0415
        from synthorg.engine.initiative.rollup import (  # noqa: PLC0415
            ProjectRollupService,
        )

        service = ProjectRollupService(
            persistence=persistence,
            plan_status_writer=PlanService(
                repo=persistence.plans, clock=app_state.clock
            ),
            clock=app_state.clock,
            ship_retro_capture=_build_ship_retro_capture(app_state),
        )
        # Register the observer BEFORE committing the service to state, so a
        # failure here leaves the service unwired and a re-run retries cleanly
        # rather than committing a service whose events never reach it. The
        # trade is deliberate: a failure between these two lines would let a
        # re-run register twice, which is why the state write follows
        # immediately and does no work of its own.
        task_engine.register_observer(service.on_task_state_changed)
        app_state.wire(EngineStateSlice, project_rollup_service=service)
    except Exception as exc:  # noqa: BLE001 -- best-effort wiring: log, continue
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="project_rollup_service",
            note="rollup wiring failed; initiative status will not advance",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="project_rollup_service", note="wired")


def _build_ship_retro_capture(app_state: AppState) -> RetroCapturePort | None:
    """Build the SHIP-time retrospective capture collaborator, or ``None``.

    The consuming tail of the loop needs both memory layers to write to; when
    either the agent-memory backend or org memory is unwired (an empty company,
    or memory switched off), the rollup still advances status, it just does not
    feed a retrospective back. Best-effort: any construction fault degrades to
    an unwired tail rather than failing the rollup wiring.

    Returns:
        A :class:`ShipRetroCaptureService`, or ``None`` when its dependencies
        are not all present.
    """
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.core.agent import AgentIdentity  # noqa: PLC0415
    from synthorg.engine.initiative.retro_capture import (  # noqa: PLC0415
        ShipRetroCaptureService,
    )
    from synthorg.hr.state import agent_registry_of  # noqa: PLC0415
    from synthorg.memory.state import (  # noqa: PLC0415
        memory_backend_or_none,
        org_memory_backend_of,
    )
    from synthorg.providers.protocol import CompletionProvider  # noqa: PLC0415
    from synthorg.providers.state import provider_registry_of  # noqa: PLC0415
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    memory_backend = memory_backend_or_none(app_state)
    org_backend = org_memory_backend_of(app_state)
    if memory_backend is None or org_backend is None:
        logger.info(
            API_APP_STARTUP,
            service="ship_retro_capture",
            note="retro tail not wired; memory or org backend absent",
        )
        return None

    # The optional retro tail must never fail the rollup wiring proper: its own
    # dependency accessors (provider/agent registry, cost tracker, resolver)
    # raise if a slice is not yet wired, so a fault here degrades to an unwired
    # tail rather than propagating into the caller's rollup-wiring try/except.
    try:
        registry = provider_registry_of(app_state)

        def _select_provider(identity: AgentIdentity) -> CompletionProvider:
            # The lead's own bound provider, re-resolved live so a provider
            # hot-swap is reflected without rebuilding the service (mirrors the
            # decomposition owner-provider selector).
            return registry.get(identity.model.provider)

        return ShipRetroCaptureService(
            agent_registry=agent_registry_of(app_state),
            memory_backend=memory_backend,
            org_backend=org_backend,
            provider_selector=_select_provider,
            default_provider=registry.default_provider(),
            cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
            config_resolver=config_resolver_of(app_state),
            clock=app_state.clock,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="ship_retro_capture",
            note="retro tail not wired; construction failed, rollup still advances",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
