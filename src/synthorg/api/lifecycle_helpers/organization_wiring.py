# module-kind: code
"""On-startup wiring for the organization MCP services.

The company-read and role-version facades project the durable org surface
(the settings-composed config resolver + ``OrgMutationService`` for company
reads/writes, and the per-entity version repositories for history), so they
wire after settings composition + persistence connect rather than at
construction. The settings-backed ``TeamService`` (which both reads and
writes ``company.departments[*].teams``) wires here too but is gated only on
a composed settings service, independently of persistence. Each wire is
best-effort + idempotent: a missing dependency leaves the facade absent and
its MCP tools 503 until the operator fixes the boot. An absent dependency
returns early (silent); a construction that *throws* is logged at ERROR, not
WARNING, since it is a real boot defect the operator must fix rather than
routine degradation.
"""

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_SERVICE_AUTO_WIRE_FAILED,
    API_SERVICE_AUTO_WIRED,
)
from synthorg.organization.state import OrganizationStateSlice
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.settings.state import SettingsStateSlice

logger = get_logger(__name__)


async def wire_team_service(app_state: AppState) -> None:
    """Wire the settings-backed ``TeamService`` once settings exist.

    ``TeamService`` reads/writes teams through the company-departments
    settings blob, so it activates once a settings service is composed; a
    settings-less boot leaves the synthorg_teams_* tools 503.

    Args:
        app_state: Application state holding the organization slice.
    """
    org = app_state.slice(OrganizationStateSlice)
    if (
        org.team_service is not None
        or app_state.slice(SettingsStateSlice).settings_service is None
    ):
        return
    try:
        from synthorg.organization._team_service import TeamService  # noqa: PLC0415

        app_state.wire(
            OrganizationStateSlice,
            team_service=TeamService(app_state=app_state),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="team_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="team_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def wire_company_read_service(
    app_state: AppState,
    persistence: PersistenceBackend | None,
    *,
    connected: bool,
) -> None:
    """Wire the company-read facade once the org surface it projects exists.

    Args:
        app_state: Application state holding the organization slice.
        persistence: Backend the version history is read through.
        connected: Whether that backend is connected. History is left absent
            rather than half-wired when it is not, so the facade still serves
            the reads that need no history.
    """
    org = app_state.slice(OrganizationStateSlice)
    resolver = app_state.slice(SettingsStateSlice).config_resolver
    org_mutation = app_state.slice(ApiCoreStateSlice).org_mutation_service
    if org.company_read_service is not None or resolver is None or org_mutation is None:
        return
    try:
        from synthorg.organization.services import CompanyReadService  # noqa: PLC0415

        app_state.wire(
            OrganizationStateSlice,
            company_read_service=CompanyReadService(
                org_mutation=org_mutation,
                config_resolver=resolver,
                company_versions=(
                    persistence.company_versions
                    if connected and persistence is not None
                    else None
                ),
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="company_read_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="company_read_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def wire_role_version_service(
    app_state: AppState,
    persistence: PersistenceBackend,
) -> None:
    """Wire the role-history facade over a connected backend.

    Args:
        app_state: Application state holding the organization slice.
        persistence: Connected backend the role versions are read from.
    """
    if app_state.slice(OrganizationStateSlice).role_version_service is not None:
        return
    try:
        from synthorg.organization.services import RoleVersionService  # noqa: PLC0415

        app_state.wire(
            OrganizationStateSlice,
            role_version_service=RoleVersionService(
                role_versions=persistence.role_versions,
            ),
        )
        logger.info(API_SERVICE_AUTO_WIRED, service="role_version_service")
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.error(
            API_SERVICE_AUTO_WIRE_FAILED,
            service="role_version_service",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = [
    "wire_company_read_service",
    "wire_role_version_service",
    "wire_team_service",
]
