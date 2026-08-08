# module-kind: orchestrator
"""Startup wiring for the initiative rollup service and its tail.

Five activations, because they converge at five different times.
:func:`wire_project_rollup_service` constructs the
:class:`ProjectRollupService` once the task engine and persistence exist, and
registers it as a :class:`TaskEngine` observer so a task reaching a terminal
status advances the plan, the project, and the objective task behind it. That
happens well before setup has configured a provider, so the rollup it builds is
deliberately tailless.

The four ``attach_*`` functions fill each tail collaborator in later, as the
work pipeline, provider registry, coordinator and memory backends arrive. Each
is its own subsystem, and each is probed from what it installed: folding them
into one made a wired rollup stand for a wired tail, and a reconciler never
revisits what it reads as converged. Folding the four into a single tail
subsystem repeated the mistake one level down, because the union of three
collaborators' requirements became a precondition for any of them: a boot with
no coordinator got no integrate stage either.

All are best-effort and idempotent. A re-run of the first is guarded by the
state slice, so the observer is never registered twice; a re-run of any
``attach_*`` returns early once its own collaborator is present.
"""

from collections.abc import Callable

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
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
        SubsystemDeclinedError: No persistence backend or no task engine,
            the two the rollup is assembled from.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    persistence = app_state.slice(PersistenceStateSlice).backend
    if app_state.slice(EngineStateSlice).project_rollup_service is not None:
        # Already wired: never re-register the observer (register_observer
        # appends unconditionally, so a re-run would double-fire it). The tail
        # is not this function's to fill; the four ``initiative_*`` subsystems
        # own it, and owning it here too would be a second wiring path onto
        # the same collaborators.
        return

    task_engine = app_state.slice(EngineStateSlice).task_engine
    if persistence is None:
        msg = "no persistence backend; the rollup reads plan and task rows"
        raise SubsystemDeclinedError(msg)
    if task_engine is None:
        msg = "no task engine; the rollup observes its state changes"
        raise SubsystemDeclinedError(msg)
    try:
        from synthorg.api.services.plan_service_factory import (  # noqa: PLC0415
            build_plan_service,
        )

        # Deliberately tailless. Every tail collaborator needs the provider
        # registry, the work pipeline or the coordinator, none of which exist
        # this early, so building them here would only ever produce the empty
        # result the tail subsystem then has to replace.
        service = ProjectRollupService(
            persistence=persistence,
            plan_status_writer=build_plan_service(persistence, clock=app_state.clock),
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


async def attach_replan_trigger(app_state: AppState) -> None:
    """Attach the stalled-initiative replan trigger onto the wired rollup.

    The activation the ``initiative_replan`` subsystem declares. Its own
    dependency is the coordinator, and a boot without one leaves a stalled
    initiative visible for the operator while integrate and evaluate carry on.

    Raises:
        SubsystemDeclinedError: No coordinator to run the replan through.
    """
    from synthorg.api.lifecycle_helpers.initiative_tail_wiring import (  # noqa: PLC0415
        build_replan_trigger,
    )

    resolved = _tail_target(app_state, ProjectRollupService.has_replan_trigger)
    if resolved is None:
        return
    persistence, rollup = resolved
    trigger = build_replan_trigger(app_state, persistence)
    if trigger is None:
        msg = "no coordinator; a replan re-dispatches through it"
        raise SubsystemDeclinedError(msg)
    rollup.attach_tail(replan_trigger=trigger)
    _log_attached("initiative_replan_trigger")


async def attach_integration_stage(app_state: AppState) -> None:
    """Attach the INTEGRATE stage onto the wired rollup.

    The activation the ``initiative_integrate`` subsystem declares. Its own
    dependency is the work pipeline, because the assembly job is an ordinary
    task; without it a plan parks at ``INTEGRATING``.

    Raises:
        SubsystemDeclinedError: No work pipeline to dispatch the assembly
            task through.
    """
    from synthorg.api.lifecycle_helpers.initiative_tail_wiring import (  # noqa: PLC0415
        build_integration_stage,
    )

    resolved = _tail_target(app_state, ProjectRollupService.has_integration)
    if resolved is None:
        return
    persistence, rollup = resolved
    stage = build_integration_stage(app_state, persistence)
    if stage is None:
        msg = "no work pipeline; the assembly job is an ordinary task"
        raise SubsystemDeclinedError(msg)
    rollup.attach_tail(integration=stage)
    _log_attached("initiative_integration_stage")


async def attach_evaluation_stage(app_state: AppState) -> None:
    """Attach the EVALUATE stage onto the wired rollup.

    The activation the ``initiative_evaluate`` subsystem declares. Its own
    dependency is a provider to judge with; without one a plan parks at
    ``EVALUATING``, which is the honest outcome for an initiative nobody scored.

    Raises:
        SubsystemDeclinedError: No provider bound to judge with.
    """
    from synthorg.api.lifecycle_helpers.initiative_tail_wiring import (  # noqa: PLC0415
        build_evaluation_stage,
    )
    from synthorg.api.services.plan_service_factory import (  # noqa: PLC0415
        build_plan_service,
    )

    resolved = _tail_target(app_state, ProjectRollupService.has_evaluation)
    if resolved is None:
        return
    persistence, rollup = resolved
    stage = build_evaluation_stage(
        app_state,
        persistence,
        plan_status_writer=build_plan_service(persistence, clock=app_state.clock),
        # Read per verdict, not captured: the trigger is its own subsystem and
        # may attach after this stage, and a captured ``None`` would park every
        # unmet initiative for the life of the process.
        replan_trigger=rollup.replan_trigger,
        reconcile=rollup,
    )
    if stage is None:
        msg = "no provider bound for evaluation; a verdict is an LLM call"
        raise SubsystemDeclinedError(msg)
    rollup.attach_tail(evaluation=stage)
    _log_attached("initiative_evaluation_stage")


async def attach_ship_retro_capture(app_state: AppState) -> None:
    """Attach the SHIP-time retrospective capture onto the wired rollup.

    The activation the ``initiative_retro_capture`` subsystem declares. It
    needs both memory layers, which converge on their own schedule, so it is
    probed separately: counted under a stage's liveness, a tail that came up
    while memory was blocked would read as converged with the retrospective
    silently never firing.

    Raises:
        SubsystemDeclinedError: Raised by the builder, naming which cause
            stopped it: a missing memory layer, or a construction that
            failed on some other collaborator. The two are reported apart so
            an operator is not sent to inspect memory over an unwired
            provider registry.
    """
    resolved = _tail_target(app_state, ProjectRollupService.has_retro_capture)
    if resolved is None:
        return
    _, rollup = resolved
    rollup.attach_tail(ship_retro_capture=_build_ship_retro_capture(app_state))
    _log_attached("ship_retro_capture")


async def detach_ship_retro_capture(app_state: AppState) -> None:
    """Drop the retrospective capture so the next pass rebuilds it.

    It captured both memory backends at construction, so once either is
    replaced the capture writes into layers nothing else reads. Detaching is
    also what the reconciler reads as the subsystem being down, since liveness
    comes from the rollup's own attachment record.
    """
    from synthorg.core.lifecycle_constants import (  # noqa: PLC0415
        DEFAULT_DRAIN_TIMEOUT_SECONDS,
    )
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

    rollup = app_state.slice(EngineStateSlice).project_rollup_service
    if rollup is None:
        return
    await rollup.detach_retro_capture(timeout_sec=DEFAULT_DRAIN_TIMEOUT_SECONDS)
    logger.info(API_APP_STARTUP, service="ship_retro_capture", note="unwired")


def _tail_target(
    app_state: AppState,
    already: Callable[[ProjectRollupService], bool],
) -> tuple[PersistenceBackend, ProjectRollupService] | None:
    """Return what one tail attach needs, or ``None`` when there is nothing to do.

    ``None`` means one thing only: this collaborator is already attached, so
    a repeated pass costs nothing. An absent backend or rollup is a decline
    with a name, because folding all three into one ``None`` is how a blocked
    tail came to report "see the wiring log".

    Args:
        app_state: Application state holding the slices.
        already: Predicate reading whether the rollup carries this
            collaborator.

    Returns:
        The persistence backend and the wired rollup, or ``None`` when the
        collaborator is already attached.

    Raises:
        SubsystemDeclinedError: The backend or the rollup is absent.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415

    persistence = app_state.slice(PersistenceStateSlice).backend
    rollup = app_state.slice(EngineStateSlice).project_rollup_service
    if persistence is None:
        msg = "no persistence backend; every tail stage reads durable rows"
        raise SubsystemDeclinedError(msg)
    if rollup is None:
        msg = "no rollup service; the tail attaches onto it"
        raise SubsystemDeclinedError(msg)
    if already(rollup):
        return None
    return persistence, rollup


def _log_attached(service: str) -> None:
    """Record that one tail collaborator came online."""
    logger.info(API_APP_STARTUP, service=service, note="attached")


def _build_ship_retro_capture(app_state: AppState) -> RetroCapturePort:
    """Build the SHIP-time retrospective capture collaborator.

    The consuming tail of the loop needs both memory layers to write to; when
    either the agent-memory backend or org memory is unwired (an empty company,
    or memory switched off), the rollup still advances status, it just does not
    feed a retrospective back.

    Returns:
        A :class:`ShipRetroCaptureService`.

    Raises:
        SubsystemDeclinedError: A memory layer is absent, naming which. A
            construction fault raises its own, from
            :func:`_construct_ship_retro_capture`, so the two causes are
            never reported as the same condition.
    """
    from synthorg.memory.state import (  # noqa: PLC0415
        memory_backend_or_none,
        org_memory_backend_of,
    )

    memory_backend = memory_backend_or_none(app_state)
    org_backend = org_memory_backend_of(app_state)
    if memory_backend is None or org_backend is None:
        missing = "org memory" if memory_backend is not None else "agent memory"
        if memory_backend is None and org_backend is None:
            missing = "agent memory and org memory"
        note = f"{missing} absent; the retrospective writes into both layers"
        logger.info(API_APP_STARTUP, service="ship_retro_capture", note=note)
        raise SubsystemDeclinedError(note)
    return _construct_ship_retro_capture(app_state, memory_backend, org_backend)


def _construct_ship_retro_capture(
    app_state: AppState,
    memory_backend: MemoryBackend,
    org_backend: OrgMemoryBackend,
) -> RetroCapturePort:
    """Construct the capture service, declining by name on any fault.

    Kept off :func:`_build_ship_retro_capture` so the two causes stay apart:
    the dependency accessors (provider / agent registry, cost tracker,
    resolver) raise if a slice is not yet wired, and reporting that as the
    memory-absent decline would send an operator to inspect memory over an
    unwired provider registry.

    Returns:
        A :class:`ShipRetroCaptureService`.

    Raises:
        SubsystemDeclinedError: A collaborator the service reads through is
            not wired, naming the failure type.
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
            cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
            config_resolver=config_resolver_of(app_state),
            clock=app_state.clock,
        )
    except Exception as exc:
        reraise_critical(exc)
        note = (
            "retro tail not wired; construction failed "
            f"({type(exc).__name__}), rollup still advances"
        )
        logger.warning(
            API_APP_STARTUP,
            service="ship_retro_capture",
            note=note,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        # Named separately from the memory-absent decline: an operator whose
        # provider registry is unwired must not be sent to inspect memory.
        raise SubsystemDeclinedError(note) from exc
