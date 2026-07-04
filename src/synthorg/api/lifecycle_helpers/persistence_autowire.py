# module-kind: code
"""Persistence-gated service auto-wiring for the startup lifecycle.

Each helper wires one service that needs a connected persistence
backend plus its repositories, composing the service into its owning
state slice. They run once at ``on_startup`` (after ``persistence.connect()``)
and are idempotent: a slice field already set short-circuits, so a
re-entered lifespan (shared-app test fixtures) does not double-wire.

Every helper keeps its own ``try``/``except`` + ``reraise_critical`` +
warning log so a transient failure in one service never aborts the
others; the controllers behind an unwired service surface 503 until the
operator fixes the underlying configuration and reboots.
"""

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.lifecycle_helpers.persistence_facade_autowire import (
    wire_persistence_facades,
)
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.state import HrStateSlice
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_SERVICE_AUTO_WIRE_FAILED,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


async def _wire_oauth_state_service(
    app_state: AppState,
    persistence: PersistenceBackend | None,
) -> None:
    """Wire ``OAuthStateService`` once persistence is connected.

    Owns the only durable write for OAuth-flow initiation so the
    ``SECURITY_OAUTH_STATE_PERSISTED`` event fires alongside every save.
    """
    if persistence is None or not getattr(persistence, "is_connected", False):
        return
    if app_state.slice(
        IntegrationsStateSlice
    ).oauth_state_service is not None or not hasattr(persistence, "oauth_states"):
        return
    try:
        from synthorg.integrations.oauth.state_service import (  # noqa: PLC0415
            OAuthStateService,
        )

        app_state.wire(
            IntegrationsStateSlice,
            oauth_state_service=OAuthStateService(repo=persistence.oauth_states),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="oauth_state_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="oauth_state_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_training_plan_service(
    app_state: AppState,
    persistence: PersistenceBackend | None,
) -> None:
    """Wire ``TrainingPlanService`` once persistence is connected.

    Centralises every plan-CRUD write the controller previously made
    directly so audit logging cannot regress when a new write path lands.
    """
    if persistence is None or not getattr(persistence, "is_connected", False):
        return
    if (
        app_state.slice(HrStateSlice).training_plan_service is not None
        or not hasattr(persistence, "training_plans")
        or not hasattr(persistence, "training_results")
    ):
        return
    try:
        from synthorg.hr.training.plan_service import (  # noqa: PLC0415
            TrainingPlanService,
        )

        app_state.wire(
            HrStateSlice,
            training_plan_service=TrainingPlanService(
                plan_repo=persistence.training_plans,
                result_repo=persistence.training_results,
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="training_plan_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="training_plan_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_workflow_rollback_service(
    app_state: AppState,
    persistence: PersistenceBackend | None,
) -> None:
    """Wire ``WorkflowRollbackService`` once persistence is connected.

    Centralises the live save + post-rollback snapshot writes the
    controller previously made directly so audit logging cannot regress
    when a new write path lands in the rollback contract.
    """
    if persistence is None or not getattr(persistence, "is_connected", False):
        return
    if (
        app_state.slice(ApiCoreStateSlice).workflow_rollback_service is not None
        or not hasattr(persistence, "workflow_definitions")
        or not hasattr(persistence, "workflow_versions")
    ):
        return
    try:
        from synthorg.api.services.workflow_rollback_service import (  # noqa: PLC0415
            WorkflowRollbackService,
        )

        app_state.wire(
            ApiCoreStateSlice,
            workflow_rollback_service=WorkflowRollbackService(
                definition_repo=persistence.workflow_definitions,
                version_repo=persistence.workflow_versions,
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="workflow_rollback_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="workflow_rollback_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_workflow_service(
    app_state: AppState,
    persistence: PersistenceBackend | None,
) -> None:
    """Wire the base ``WorkflowService`` onto ``EngineStateSlice``.

    ``workflow_service_of`` (meta-apply wiring + the MCP workflow-definition
    handlers) resolves this slice; without the wire it raised
    ``ServiceUnavailableError: Workflow Service not configured``. The REST
    workflow controllers build their own per-request instance, so only the
    engine-level consumers depended on this slice being populated.
    """
    if persistence is None or not getattr(persistence, "is_connected", False):
        return
    if (
        app_state.slice(EngineStateSlice).workflow_service is not None
        or not hasattr(persistence, "workflow_definitions")
        or not hasattr(persistence, "workflow_versions")
    ):
        return
    try:
        from synthorg.engine.workflow.service import WorkflowService  # noqa: PLC0415
        from synthorg.versioning import VersioningService  # noqa: PLC0415

        app_state.wire(
            EngineStateSlice,
            workflow_service=WorkflowService(
                definition_repo=persistence.workflow_definitions,
                version_repo=persistence.workflow_versions,
                versioning_service=VersioningService(persistence.workflow_versions),
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="workflow_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="workflow_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_workflow_version_service(
    app_state: AppState,
    persistence: PersistenceBackend | None,
) -> None:
    """Wire ``WorkflowVersionService`` once persistence is connected.

    Lets the workflow-version-history controller read snapshots through
    the service facade rather than reaching into
    ``persistence.workflow_versions`` directly.
    """
    if persistence is None or not getattr(persistence, "is_connected", False):
        return
    if app_state.slice(
        EngineStateSlice
    ).workflow_version_service is not None or not hasattr(
        persistence, "workflow_versions"
    ):
        return
    try:
        from synthorg.engine.workflow.version_service import (  # noqa: PLC0415
            WorkflowVersionService,
        )

        app_state.wire(
            EngineStateSlice,
            workflow_version_service=WorkflowVersionService(
                version_repo=persistence.workflow_versions,
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="workflow_version_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="workflow_version_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_agent_version_service(
    app_state: AppState,
    persistence: PersistenceBackend | None,
) -> None:
    """Wire ``AgentVersionService`` once persistence is connected.

    Mirrors the workflow-version wiring for the agent-identity version
    history controller, gated on the same persistence readiness.
    """
    if persistence is None or not getattr(persistence, "is_connected", False):
        return
    if app_state.slice(HrStateSlice).agent_version_service is not None or not hasattr(
        persistence, "identity_versions"
    ):
        return
    try:
        from synthorg.hr.identity.version_service import (  # noqa: PLC0415
            AgentVersionService,
        )

        app_state.wire(
            HrStateSlice,
            agent_version_service=AgentVersionService(
                version_repo=persistence.identity_versions,
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="agent_version_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="agent_version_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_subworkflow_service(
    app_state: AppState,
    persistence: PersistenceBackend | None,
) -> None:
    """Wire ``SubworkflowService`` once persistence is connected.

    Lets the subworkflow MCP handlers reach the control-plane facade
    (paginated list, version resolution, parent-cascade delete, audit
    emission) instead of falling through to a ``capability_gap`` envelope.
    """
    if persistence is None or not getattr(persistence, "is_connected", False):
        return
    if app_state.slice(EngineStateSlice).subworkflow_service is not None or not hasattr(
        persistence, "subworkflows"
    ):
        return
    try:
        from synthorg.engine.workflow.subworkflow_registry import (  # noqa: PLC0415
            SubworkflowRegistry,
        )
        from synthorg.engine.workflow.subworkflow_service import (  # noqa: PLC0415
            SubworkflowService,
        )

        app_state.wire(
            EngineStateSlice,
            subworkflow_service=SubworkflowService(
                registry=SubworkflowRegistry(persistence.subworkflows),
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="subworkflow_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="subworkflow_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_evaluation_version_service(
    app_state: AppState,
    persistence: PersistenceBackend | None,
) -> None:
    """Wire ``EvaluationVersionService`` once persistence is connected.

    Mirrors the workflow-version wiring for the evaluation-config version
    history MCP handlers, gated on the same persistence readiness.
    """
    if persistence is None or not getattr(persistence, "is_connected", False):
        return
    if app_state.slice(
        EngineStateSlice
    ).evaluation_version_service is not None or not hasattr(
        persistence, "evaluation_config_versions"
    ):
        return
    try:
        from synthorg.engine.quality.mcp_services import (  # noqa: PLC0415
            EvaluationVersionService,
        )

        app_state.wire(
            EngineStateSlice,
            evaluation_version_service=EvaluationVersionService(
                persistence=persistence,
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="evaluation_version_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="evaluation_version_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_personality_service(
    app_state: AppState,
    persistence: PersistenceBackend | None,
) -> None:
    """Wire ``PersonalityService`` once persistence is connected.

    Wraps the same custom-preset repo the REST personalities controller
    builds per request, so the synthorg_personalities_* MCP tools read the
    identical builtin + custom preset surface instead of 503-ing.
    """
    if persistence is None or not getattr(persistence, "is_connected", False):
        return
    if app_state.slice(HrStateSlice).personality_service is not None or not hasattr(
        persistence, "custom_presets"
    ):
        return
    try:
        from synthorg.hr.personalities.service import (  # noqa: PLC0415
            PersonalityService,
        )
        from synthorg.templates.preset_service import (  # noqa: PLC0415
            PersonalityPresetService,
        )

        app_state.wire(
            HrStateSlice,
            personality_service=PersonalityService(
                presets=PersonalityPresetService(
                    repository=persistence.custom_presets,
                ),
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="personality_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="personality_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_activity_feed_service(
    app_state: AppState,
    persistence: PersistenceBackend | None,
) -> None:
    """Wire ``ActivityFeedService`` once persistence is connected.

    Aggregates the lifecycle-event / performance / cost sources behind the
    synthorg_agents_get_activity + synthorg_activities_list tools. The
    optional tool-invocation and delegation sources stay absent until their
    own wiring lands, so the feed degrades to the present sources rather
    than 503-ing.
    """
    if persistence is None or not getattr(persistence, "is_connected", False):
        return
    hr = app_state.slice(HrStateSlice)
    if (
        hr.activity_feed_service is not None
        or hr.performance_tracker is None
        or not hasattr(persistence, "lifecycle_events")
    ):
        return
    try:
        from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
        from synthorg.hr.activity_service import ActivityFeedService  # noqa: PLC0415
        from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

        app_state.wire(
            HrStateSlice,
            activity_feed_service=ActivityFeedService(
                performance_tracker=hr.performance_tracker,
                lifecycle_repo=persistence.lifecycle_events,
                cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
                config_resolver=app_state.slice(SettingsStateSlice).config_resolver,
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="activity_feed_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="activity_feed_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def wire_persistence_services(
    app_state: AppState,
    persistence: PersistenceBackend | None,
) -> None:
    """Run every persistence-gated auto-wire once at startup.

    Each helper is independent and best-effort; a failure in one logs
    and leaves the rest to run.
    """
    await _wire_oauth_state_service(app_state, persistence)
    await _wire_training_plan_service(app_state, persistence)
    await _wire_workflow_rollback_service(app_state, persistence)
    await _wire_workflow_service(app_state, persistence)
    await _wire_workflow_version_service(app_state, persistence)
    await _wire_agent_version_service(app_state, persistence)
    await _wire_subworkflow_service(app_state, persistence)
    await _wire_evaluation_version_service(app_state, persistence)
    await _wire_personality_service(app_state, persistence)
    await _wire_activity_feed_service(app_state, persistence)
    await wire_persistence_facades(app_state)
