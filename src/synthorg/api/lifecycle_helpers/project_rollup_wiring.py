# module-kind: orchestrator
"""Startup wiring for the initiative rollup service and its tail.

Two activations, because the two converge at different times.
:func:`wire_project_rollup_service` constructs the
:class:`ProjectRollupService` once the task engine and persistence exist, and
registers it as a :class:`TaskEngine` observer so a task reaching a terminal
status advances the plan, the project, and the objective task behind it. That
happens well before setup has configured a provider, so the rollup it builds is
deliberately tailless.

:func:`attach_initiative_tail` fills the tail in later, once the provider
registry, work pipeline and coordinator exist. It is a separate subsystem
rather than a re-run of the first: liveness is read from what activation
installed, so folding both into one made a wired rollup stand for a wired tail,
and a reconciler never revisits what it reads as converged.

Both are best-effort and idempotent. A re-run of the first is guarded by the
state slice, so the observer is never registered twice; a re-run of the second
returns early once the tail is full.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.initiative.ports import RetroCapturePort
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackend

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

    persistence = app_state.slice(PersistenceStateSlice).backend
    if app_state.slice(EngineStateSlice).project_rollup_service is not None:
        # Already wired: never re-register the observer (register_observer
        # appends unconditionally, so a re-run would double-fire it). The tail
        # is not this function's to fill; the ``initiative_tail`` subsystem
        # owns it, and owning it here too would be a second wiring path onto
        # the same collaborators.
        return

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

        # Deliberately tailless. Every tail collaborator needs the provider
        # registry, the work pipeline or the coordinator, none of which exist
        # this early, so building them here would only ever produce the empty
        # result the tail subsystem then has to replace.
        service = ProjectRollupService(
            persistence=persistence,
            plan_status_writer=PlanService(
                repo=persistence.plans, clock=app_state.clock
            ),
            clock=app_state.clock,
            task_engine=task_engine,
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


async def attach_initiative_tail(app_state: AppState) -> None:
    """Bring the initiative tail online on the already-wired rollup.

    The activation the ``initiative_tail`` subsystem declares. It is separate
    from the rollup's own activation because the two converge at different
    times: the rollup needs only persistence and the task engine, both of which
    are up before setup has configured a provider, while every tail stage needs
    the provider registry, the work pipeline or the coordinator. Declaring them
    as one subsystem made the rollup's early success stand for the tail's, and
    the reconciler never revisits a subsystem it reads as converged.

    A no-op when the rollup is absent or already carries a full tail, so a
    repeated pass costs nothing.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    persistence = app_state.slice(PersistenceStateSlice).backend
    rollup = app_state.slice(EngineStateSlice).project_rollup_service
    if persistence is None or rollup is None or rollup.has_full_tail():
        return
    _attach_tail(app_state, persistence, rollup)


def _attach_tail(
    app_state: AppState,
    persistence: PersistenceBackend,
    rollup: ProjectRollupService,
) -> None:
    """Bring the tail online on an already-wired rollup, best-effort."""
    from synthorg.api.lifecycle_helpers.initiative_tail_wiring import (  # noqa: PLC0415
        build_evaluation_stage,
        build_integration_stage,
        build_replan_trigger,
    )
    from synthorg.api.services.plan_service import PlanService  # noqa: PLC0415

    try:
        plan_writer = PlanService(repo=persistence.plans, clock=app_state.clock)
        rollup.attach_tail(
            replan_trigger=build_replan_trigger(app_state, persistence),
            integration=build_integration_stage(app_state, persistence),
            # A factory, not an instance: the stage captures the replan trigger
            # for the life of the process, so it must be built against the one
            # the rollup keeps rather than one this call built and it discarded.
            evaluation=lambda trigger: build_evaluation_stage(
                app_state,
                persistence,
                plan_status_writer=plan_writer,
                replan_trigger=trigger,
                reconcile=rollup,
            ),
            # Needs the same registries the stages do, so a rollup built before
            # they existed has none and only this pass can supply it.
            ship_retro_capture=_build_ship_retro_capture(app_state),
        )
    except Exception as exc:  # noqa: BLE001 -- best-effort wiring: log, continue
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="initiative_tail",
            note="tail attach failed; the plan will park in the tail",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(API_APP_STARTUP, service="initiative_tail", note="attached")


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
    from synthorg.memory.state import (  # noqa: PLC0415
        memory_backend_or_none,
        org_memory_backend_of,
    )

    memory_backend = memory_backend_or_none(app_state)
    org_backend = org_memory_backend_of(app_state)
    if memory_backend is None or org_backend is None:
        logger.info(
            API_APP_STARTUP,
            service="ship_retro_capture",
            note="retro tail not wired; memory or org backend absent",
        )
        return None
    return _construct_ship_retro_capture(app_state, memory_backend, org_backend)


def _construct_ship_retro_capture(
    app_state: AppState,
    memory_backend: MemoryBackend,
    org_backend: OrgMemoryBackend,
) -> RetroCapturePort | None:
    """Construct the capture service, degrading to ``None`` on any fault.

    Kept off :func:`_build_ship_retro_capture` so the optional retro tail never
    fails the rollup wiring proper: the dependency accessors (provider / agent
    registry, cost tracker, resolver) raise if a slice is not yet wired, so a
    fault here degrades to an unwired tail rather than propagating into the
    caller's rollup-wiring try/except.

    Returns:
        A :class:`ShipRetroCaptureService`, or ``None`` when construction faults.
    """
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.core.agent import AgentIdentity  # noqa: PLC0415
    from synthorg.engine.initiative.retro_capture import (  # noqa: PLC0415
        ShipRetroCaptureService,
    )
    from synthorg.hr.state import agent_registry_of  # noqa: PLC0415
    from synthorg.providers.protocol import CompletionProvider  # noqa: PLC0415
    from synthorg.providers.state import provider_registry_of  # noqa: PLC0415
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    try:
        registry = provider_registry_of(app_state)

        def _select_provider(identity: AgentIdentity) -> CompletionProvider:
            # The lead's own bound provider, re-resolved live so a provider
            # hot-swap is reflected without rebuilding the service.
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
