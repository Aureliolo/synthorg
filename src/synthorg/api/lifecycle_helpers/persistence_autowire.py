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
from synthorg.api.state import AppState
from synthorg.backup.state import BackupStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.state import EngineStateSlice
from synthorg.hr.state import HrStateSlice
from synthorg.infrastructure.state import FacadesStateSlice
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_SERVICE_AUTO_WIRE_FAILED,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.ontology.state import OntologyStateSlice
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


async def _wire_user_facade_service(app_state: AppState) -> None:
    """Wire ``UserFacadeService`` once the auth service is present.

    The auth service is composed inside ``_init_persistence`` (which runs
    in ``_safe_startup`` before this helper), so by here it is wired
    whenever authentication is configured; the user MCP read tools 503
    until then.
    """
    api_core = app_state.slice(ApiCoreStateSlice)
    if (
        app_state.slice(FacadesStateSlice).user_facade_service is not None
        or api_core.auth_service is None
    ):
        return
    try:
        from synthorg.infrastructure.services import UserFacadeService  # noqa: PLC0415

        app_state.wire(
            FacadesStateSlice,
            user_facade_service=UserFacadeService(auth_service=api_core.auth_service),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="user_facade_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="user_facade_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_backup_facade_service(app_state: AppState) -> None:
    """Wire ``BackupFacadeService`` once the backup service has started.

    The backup service reaches ``BackupStateSlice.service`` inside
    ``_safe_startup`` (gated on the scheduler starting), so this facade
    wires only when backups are actually running in this deployment.
    """
    backup = app_state.slice(BackupStateSlice)
    if (
        app_state.slice(FacadesStateSlice).backup_facade_service is not None
        or backup.service is None
    ):
        return
    try:
        from synthorg.infrastructure.services import (  # noqa: PLC0415
            BackupFacadeService,
        )

        app_state.wire(
            FacadesStateSlice,
            backup_facade_service=BackupFacadeService(service=backup.service),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="backup_facade_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="backup_facade_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_ontology_facade_service(app_state: AppState) -> None:
    """Wire ``OntologyFacadeService`` once the ontology service is present.

    ``_wire_ontology_service`` composes the ontology service inside
    ``_safe_startup``; this facade projects it for the ontology MCP read
    tools, which 503 until the underlying service wires.
    """
    ontology = app_state.slice(OntologyStateSlice)
    if (
        app_state.slice(FacadesStateSlice).ontology_facade_service is not None
        or ontology.service is None
    ):
        return
    try:
        from synthorg.integrations.mcp_facades import (  # noqa: PLC0415
            OntologyFacadeService,
        )

        app_state.wire(
            FacadesStateSlice,
            ontology_facade_service=OntologyFacadeService(ontology=ontology.service),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="ontology_facade_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="ontology_facade_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_mcp_catalog_facade_service(app_state: AppState) -> None:
    """Wire ``MCPCatalogFacadeService`` once its catalog + repo are present.

    The catalog service and installation repo are wired onto the
    integrations slice during ``_init_persistence``; the MCP-catalog read/
    install tools 503 until both are available.
    """
    integrations = app_state.slice(IntegrationsStateSlice)
    if (
        app_state.slice(FacadesStateSlice).mcp_catalog_facade_service is not None
        or integrations.mcp_catalog_service is None
        or integrations.mcp_installations_repo is None
    ):
        return
    try:
        from synthorg.integrations.mcp_facades import (  # noqa: PLC0415
            MCPCatalogFacadeService,
        )

        app_state.wire(
            FacadesStateSlice,
            mcp_catalog_facade_service=MCPCatalogFacadeService(
                catalog=integrations.mcp_catalog_service,
                installations=integrations.mcp_installations_repo,
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="mcp_catalog_facade_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="mcp_catalog_facade_service",
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
    await _wire_user_facade_service(app_state)
    await _wire_backup_facade_service(app_state)
    await _wire_ontology_facade_service(app_state)
    await _wire_mcp_catalog_facade_service(app_state)
