# module-kind: orchestrator
"""Startup wiring for the initiative loop's tail: replan, integrate, evaluate.

Three collaborators the rollup fires but does not own. Each is built
independently and degrades independently: a boot missing the coordinator gets
no auto-replan but still integrates, a boot with no work pipeline still
evaluates, and a boot with none of them still advances plan and project status
(parking the plan visibly in the tail rather than completing it).

The replan adapter is where the layering inverts: ``replan_initiative`` owns
the compensated ordering across the plan service, the project repository, and
the task engine, and it lives in the API. The engine states what it needs
(``InitiativeReplanPort``) and this closure supplies it, so there is one
re-plan path whether a human or the organisation asked for it.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan, PlanItem
from synthorg.engine.initiative.ports import (
    EvaluationPort,
    IntegrationPort,
    PlanReconcilePort,
    PlanStatusWriter,
    ReplanTriggerPort,
    ReplanTriggerResolver,
    SkeletonPort,
)
from synthorg.engine.loop_protocol import ShutdownChecker
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


class _ReplanAdapter:
    """Satisfies ``InitiativeReplanPort`` over the API's re-plan orchestration."""

    __slots__ = ("_app_state",)

    def __init__(self, app_state: AppState) -> None:
        self._app_state = app_state

    async def replan(
        self,
        existing: Plan,
        *,
        items: tuple[PlanItem, ...],
        requested_by: str,
        replan_generation: int,
    ) -> Plan:
        """Retire *existing* and open the successor that replaces it.

        Returns:
            The persisted successor, awaiting review.
        """
        from synthorg.api.controllers._plan_replan import (  # noqa: PLC0415
            RevisionInputs,
            replan_initiative,
        )

        return await replan_initiative(
            self._app_state,
            existing,
            revision=RevisionInputs(items=items),
            requested_by=requested_by,
            replan_generation=replan_generation,
        )


def build_replan_trigger(
    app_state: AppState,
    persistence: PersistenceBackend,
) -> ReplanTriggerPort | None:
    """Build the stalled-initiative replan trigger, or ``None``.

    Needs the planner to produce a successor's items, so a boot without a
    coordinator (no provider configured) leaves a stalled initiative for the
    operator instead of replanning it.

    Returns:
        A ``ReplanTriggerService``, or ``None`` when its deps are absent.
    """
    from synthorg.engine.initiative.replan_trigger import (  # noqa: PLC0415
        ReplanTriggerService,
    )
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.hr.state import HrStateSlice  # noqa: PLC0415
    from synthorg.security.action_types import ActionTypeRegistry  # noqa: PLC0415
    from synthorg.security.autonomy.resolver import AutonomyResolver  # noqa: PLC0415
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415
    from synthorg.workers.state import RuntimeStateSlice  # noqa: PLC0415

    task_engine = app_state.slice(EngineStateSlice).task_engine
    coordinator = app_state.slice(RuntimeStateSlice).coordinator
    if task_engine is None or coordinator is None:
        logger.info(
            API_APP_STARTUP,
            service="initiative_replan_trigger",
            note="auto-replan not wired; task engine or coordinator absent",
        )
        return None
    try:
        return ReplanTriggerService(
            persistence=persistence,
            task_engine=task_engine,
            decomposition_service=coordinator.decomposition_service,
            replan=_ReplanAdapter(app_state),
            config_resolver=config_resolver_of(app_state),
            # The registry itself, not a roster read here: a successor plan
            # is drafted long after boot and must be owned by whoever the org
            # staffs then.
            agent_registry=app_state.slice(HrStateSlice).agent_registry,
            autonomy_resolver=AutonomyResolver(
                registry=ActionTypeRegistry(),
                config=app_state.config.config.autonomy,
            ),
            clock=app_state.clock,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        _log_degraded("initiative_replan_trigger", "auto-replan not wired", exc)
        return None


def build_skeleton_stage(
    app_state: AppState,
    persistence: PersistenceBackend,
) -> SkeletonPort | None:
    """Build the SKELETON stage, or ``None``.

    Needs the work spine, because the contract job is an ordinary task that
    must run under the same routing, budgets and review gate as everything
    else. Without it the plan parks at SKELETON rather than dispatching, which
    is the safe direction: nothing has been built against the contract yet.

    Returns:
        A ``SkeletonStageService``, or ``None`` when its deps are absent.
    """
    from synthorg.engine.initiative.skeleton import (  # noqa: PLC0415
        SkeletonStageService,
    )
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    slice_ = app_state.slice(EngineStateSlice)
    if slice_.task_engine is None or slice_.work_pipeline is None:
        logger.info(
            API_APP_STARTUP,
            service="initiative_skeleton_stage",
            note="skeleton stage not wired; task engine or work pipeline absent",
        )
        return None
    try:
        return SkeletonStageService(
            persistence=persistence,
            task_engine=slice_.task_engine,
            pipeline=slice_.work_pipeline,
            config_resolver=config_resolver_of(app_state),
            clock=app_state.clock,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        _log_degraded("initiative_skeleton_stage", "skeleton stage not wired", exc)
        return None


def build_integration_stage(
    app_state: AppState,
    persistence: PersistenceBackend,
) -> IntegrationPort | None:
    """Build the INTEGRATE stage, or ``None``.

    Needs the work spine, because the assembly job is an ordinary task that
    must run under the same routing, budgets, and review gate as everything
    else. Without it the plan parks at INTEGRATING rather than completing.

    Returns:
        An ``IntegrationStageService``, or ``None`` when its deps are absent.
    """
    from synthorg.engine.initiative.integrate import (  # noqa: PLC0415
        IntegrationStageService,
    )
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    slice_ = app_state.slice(EngineStateSlice)
    if slice_.task_engine is None or slice_.work_pipeline is None:
        logger.info(
            API_APP_STARTUP,
            service="initiative_integration_stage",
            note="integrate stage not wired; task engine or work pipeline absent",
        )
        return None
    try:
        return IntegrationStageService(
            persistence=persistence,
            task_engine=slice_.task_engine,
            pipeline=slice_.work_pipeline,
            config_resolver=config_resolver_of(app_state),
            clock=app_state.clock,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        _log_degraded("initiative_integration_stage", "integrate stage not wired", exc)
        return None


def build_evaluation_stage(
    app_state: AppState,
    persistence: PersistenceBackend,
    *,
    plan_status_writer: PlanStatusWriter,
    replan_trigger: ReplanTriggerResolver | None,
    reconcile: PlanReconcilePort | None,
) -> EvaluationPort | None:
    """Build the EVALUATE stage, or ``None``.

    Needs a provider to judge with. Without it the plan parks at EVALUATING:
    an initiative nobody scored has not been shown to meet its objective, and
    completing it anyway is the exact lie the tail exists to remove.

    Returns:
        An ``EvaluationStageService``, or ``None`` when its deps are absent.
    """
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.core.agent import AgentIdentity  # noqa: PLC0415
    from synthorg.engine.initiative.evaluate import (  # noqa: PLC0415
        EvaluationStageService,
    )
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        agent_workspace_root_of,
    )
    from synthorg.hr.state import agent_registry_of  # noqa: PLC0415
    from synthorg.providers.protocol import CompletionProvider  # noqa: PLC0415
    from synthorg.providers.state import provider_registry_of  # noqa: PLC0415
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    try:
        registry = provider_registry_of(app_state)

        def _select_provider(identity: AgentIdentity) -> CompletionProvider:
            # Re-resolved live so a provider hot-swap is reflected without
            # rebuilding the stage.
            return registry.get(identity.model.provider)

        return EvaluationStageService(
            persistence=persistence,
            agent_registry=agent_registry_of(app_state),
            provider_selector=_select_provider,
            plan_status_writer=plan_status_writer,
            replan_trigger=replan_trigger,
            reconcile=reconcile,
            workspace_root=agent_workspace_root_of(app_state),
            cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
            shutdown_checker=_shutdown_checker(app_state),
            config_resolver=config_resolver_of(app_state),
            clock=app_state.clock,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        _log_degraded("initiative_evaluation_stage", "evaluate stage not wired", exc)
        return None


def _shutdown_checker(app_state: AppState) -> ShutdownChecker:
    """Return the graceful-shutdown signal for the judgement session.

    Without it the session cannot stop at a turn boundary, so SIGTERM
    hard-cancels it mid provider call and the whole (paid) judgement is lost
    rather than exiting cleanly.

    Returns:
        A checker over the app's shutdown manager.
    """
    return app_state.shutdown_manager.is_shutting_down


def _log_degraded(service: str, note: str, exc: Exception) -> None:
    """Warn that one tail collaborator degraded to unwired."""
    logger.warning(
        API_APP_STARTUP,
        service=service,
        note=f"{note}; construction failed, the plan will park in the tail",
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )
