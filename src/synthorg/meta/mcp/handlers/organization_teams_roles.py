"""Team and role-version MCP handlers.

Team CRUD (list / get / create / update / delete) shimming through
:func:`team_service_of`, plus the read-only role-version history
(list / get) through :func:`role_version_service_of`. ``teams_delete`` is
destructive and enforces the admin guardrail triple (confirm + reason +
actor), emitting ``MCP_ADMIN_OP_EXECUTED`` on success.
"""

from typing import TYPE_CHECKING

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.meta.mcp.domains._workflows_org_args import (
    RoleVersionsGetArgs,
    RoleVersionsListArgs,
    TeamsCreateArgs,
    TeamsDeleteArgs,
    TeamsGetArgs,
    TeamsListArgs,
    TeamsUpdateArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import (
    _map_capability,
    _to_jsonable,
    typed_args,
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    capability_gap,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    require_actor_id,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_INVOKE_SUCCESS,
)
from synthorg.organization.state import (
    OrganizationStateSlice,
    role_version_service_of,
    team_service_of,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_WHY_TEAM_NOT_WIRED = "team_service is not wired on app_state in this deployment"
_WHY_ROLE_NOT_WIRED = (
    "role_version_service is not wired on app_state in this deployment"
)


def _team_service_wired(app_state: AppState) -> bool:
    """Return whether the team service is attached to ``app_state``.

    Returns:
        ``True`` when the service slot is populated, ``False`` otherwise.
    """
    return app_state.slice(OrganizationStateSlice).team_service is not None


def _role_version_service_wired(app_state: AppState) -> bool:
    """Return whether the role-version service is attached to ``app_state``.

    Returns:
        ``True`` when the service slot is populated, ``False`` otherwise.
    """
    return app_state.slice(OrganizationStateSlice).role_version_service is not None


async def _teams_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return a paginated slice of every team across departments.

    Returns:
        Resulting string.
    """
    tool = "synthorg_teams_list"
    if not _team_service_wired(app_state):
        return capability_gap(tool, _WHY_TEAM_NOT_WIRED)
    try:
        page_args = typed_args(arguments, TeamsListArgs)
        offset, limit = page_args.offset, page_args.limit
        page, total = await team_service_of(app_state).list_teams(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok([_to_jsonable(team) for team in page], pagination=pagination)


async def _teams_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single team by ``(department, team_name)``.

    Returns:
        Resulting string.
    """
    tool = "synthorg_teams_get"
    if not _team_service_wired(app_state):
        return capability_gap(tool, _WHY_TEAM_NOT_WIRED)
    try:
        args = typed_args(arguments, TeamsGetArgs)
        record = await team_service_of(app_state).get_team(
            department=args.department,
            team_name=args.team_name,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        missing = NotFoundError(
            f"Team {args.team_name!r} not found in department {args.department!r}"
        )
        log_handler_invoke_failed(
            tool, missing, department=args.department, team_name=args.team_name
        )
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(_to_jsonable(record))


async def _teams_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a team within a department (non-destructive write).

    Returns:
        Resulting string.
    """
    tool = "synthorg_teams_create"
    if not _team_service_wired(app_state):
        return capability_gap(tool, _WHY_TEAM_NOT_WIRED)
    try:
        args = typed_args(arguments, TeamsCreateArgs)
        record = await team_service_of(app_state).create_team(
            department=args.department,
            name=args.name,
            lead=args.lead,
            members=args.members,
            actor_id=require_actor_id(actor),
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(_to_jsonable(record))


async def _teams_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Update a team (rename, change lead, replace members).

    Returns:
        Resulting string.
    """
    tool = "synthorg_teams_update"
    if not _team_service_wired(app_state):
        return capability_gap(tool, _WHY_TEAM_NOT_WIRED)
    try:
        args = typed_args(arguments, TeamsUpdateArgs)
        record = await team_service_of(app_state).update_team(
            department=args.department,
            team_name=args.team_name,
            name=args.name,
            lead=args.lead,
            members=args.members,
            actor_id=require_actor_id(actor),
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        missing = NotFoundError(
            f"Team {args.team_name!r} not found in department {args.department!r}"
        )
        log_handler_invoke_failed(
            tool, missing, department=args.department, team_name=args.team_name
        )
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(_to_jsonable(record))


async def _teams_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a team (destructive; enforces confirm + reason + actor).

    Returns:
        Resulting string.
    """
    tool = "synthorg_teams_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        args = typed_args(arguments, TeamsDeleteArgs)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    if not _team_service_wired(app_state):
        return capability_gap(tool, _WHY_TEAM_NOT_WIRED)
    actor_id = require_actor_id(resolved_actor)
    try:
        removed = await team_service_of(app_state).delete_team(
            department=args.department,
            team_name=args.team_name,
            actor_id=actor_id,
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_ADMIN_OP_EXECUTED,
                tool_name=tool,
                actor_agent_id=actor_id,
                reason=reason,
                department=args.department,
                team_name=args.team_name,
                removed=removed,
            )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok({"removed": removed})


async def _role_versions_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List a single role's version snapshots (role name is required).

    Returns:
        Resulting string.
    """
    tool = "synthorg_role_versions_list"
    if not _role_version_service_wired(app_state):
        return capability_gap(tool, _WHY_ROLE_NOT_WIRED)
    try:
        args = typed_args(arguments, RoleVersionsListArgs)
        page, total = await role_version_service_of(app_state).list_versions(
            role_name=args.role_name,
            offset=args.offset,
            limit=args.limit,
        )
        pagination = PaginationMeta(total=total, offset=args.offset, limit=args.limit)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok([_to_jsonable(v) for v in page], pagination=pagination)


async def _role_versions_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single role-version snapshot by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_role_versions_get"
    if not _role_version_service_wired(app_state):
        return capability_gap(tool, _WHY_ROLE_NOT_WIRED)
    try:
        args = typed_args(arguments, RoleVersionsGetArgs)
        version = await role_version_service_of(app_state).get_version(
            role_name=args.role_name,
            version_id=args.version_id,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if version is None:
        missing = NotFoundError(
            f"Version {args.version_id} not found for role {args.role_name!r}"
        )
        log_handler_invoke_failed(
            tool, missing, role_name=args.role_name, version_id=args.version_id
        )
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(_to_jsonable(version))
