# module-kind: code
"""Persistence-gated infrastructure read-facade auto-wiring.

The user / backup / ontology / MCP-catalog MCP read facades project
services that only reach their slices during ``_init_persistence`` /
``_safe_startup`` (the auth service, the started backup service, the
ontology service, the catalog service + installation repo). They wire
after those, so they live alongside the other persistence-gated
auto-wirers but split into their own module to keep each under the
module-size budget.

Every helper keeps its own ``try``/``except`` + ``reraise_critical`` so a
failure in one facade never aborts the others; the MCP read tools behind an
unwired facade surface 503 until the operator fixes the underlying
configuration and reboots. An absent dependency returns early (silent); a
construction that *throws* is logged at ERROR, since it is a real boot
defect the operator must fix rather than routine degradation.
"""

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.state import AppState
from synthorg.backup.state import BackupStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.infrastructure.state import FacadesStateSlice
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_SERVICE_AUTO_WIRE_FAILED,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.ontology.state import OntologyStateSlice

logger = get_logger(__name__)


async def wire_persistence_facades(app_state: AppState) -> None:
    """Wire every persistence-gated infrastructure read facade.

    Each helper is independent and best-effort; a failure in one logs
    and leaves the rest to run.
    """
    await _wire_user_facade_service(app_state)
    await _wire_backup_facade_service(app_state)
    await _wire_ontology_facade_service(app_state)
    await _wire_mcp_catalog_facade_service(app_state)


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
        logger.error(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="user_facade_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_backup_facade_service(app_state: AppState) -> None:
    """Wire ``BackupFacadeService`` once a backup service instance exists.

    The gate is solely ``BackupStateSlice.service is not None``: whenever a
    backup service was constructed onto the slice for this deployment the
    facade wires, independently of scheduler state. A deployment that never
    builds a backup service leaves the backup MCP tools 503.
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
        logger.error(
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
        logger.error(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="ontology_facade_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _wire_mcp_catalog_facade_service(app_state: AppState) -> None:
    """Wire ``MCPCatalogFacadeService`` once its catalog + repo are present.

    The catalog service, installation repo, and connection catalog are
    wired onto the integrations slice during ``_init_persistence``; the
    MCP-catalog read/install tools 503 until all three are available (the
    connection catalog is needed to validate a connection-bound install).
    """
    integrations = app_state.slice(IntegrationsStateSlice)
    if (
        app_state.slice(FacadesStateSlice).mcp_catalog_facade_service is not None
        or integrations.mcp_catalog_service is None
        or integrations.mcp_installations_repo is None
        or integrations.connection_catalog is None
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
                connection_catalog=integrations.connection_catalog,
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="mcp_catalog_facade_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="mcp_catalog_facade_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = ["wire_persistence_facades"]
