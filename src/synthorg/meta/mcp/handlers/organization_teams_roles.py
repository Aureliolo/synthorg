"""Team and role-version MCP handlers.

Team CRUD (list / get / create / update / delete) shimming through
:func:`team_service_of`, plus the read-only role-version history
(list / get) through :func:`role_version_service_of`. ``teams_delete`` is
destructive and enforces the admin guardrail triple (confirm + reason +
actor), emitting ``MCP_ADMIN_OP_EXECUTED`` on success.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.types import NotBlankStr
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
from synthorg.organization.services import UNSET, UnsetType
from synthorg.organization.state import (
    OrganizationStateSlice,
    role_version_service_of,
    team_service_of,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_TEAM_ID = "team_id"
_TY_UUID = "UUID string"
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
    """Return a paginated slice of teams.

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
    return ok([t.to_dict() for t in page], pagination=pagination)


async def _teams_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single team by UUID.

    Returns:
        Resulting string.

    Raises:
        ArgumentValidationError: When ``team_id`` is not a UUID string.
    """
    tool = "synthorg_teams_get"
    if not _team_service_wired(app_state):
        return capability_gap(tool, _WHY_TEAM_NOT_WIRED)
    try:
        team_id = typed_args(arguments, TeamsGetArgs).team_id
        try:
            UUID(team_id)
        except ValueError as uuid_exc:
            raise ArgumentValidationError(_ARG_TEAM_ID, _TY_UUID) from uuid_exc
        record = await team_service_of(app_state).get_team(team_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        missing = NotFoundError(f"Team {team_id} not found")
        log_handler_invoke_failed(tool, missing, team_id=team_id)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(record.to_dict())


async def _teams_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new team record (non-destructive write).

    Returns:
        Resulting string.
    """
    tool = "synthorg_teams_create"
    if not _team_service_wired(app_state):
        return capability_gap(tool, _WHY_TEAM_NOT_WIRED)
    try:
        args = typed_args(arguments, TeamsCreateArgs)
        record = await team_service_of(app_state).create_team(
            name=args.name,
            actor_id=require_actor_id(actor),
            department_id=args.department_id,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(record.to_dict())


async def _teams_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Update name / department on an existing team (partial patch).

    Returns:
        Resulting string.
    """
    tool = "synthorg_teams_update"
    if not _team_service_wired(app_state):
        return capability_gap(tool, _WHY_TEAM_NOT_WIRED)
    try:
        args = typed_args(arguments, TeamsUpdateArgs)
        # The MCP invoker re-materialises every field via ``model_dump`` before
        # the handler runs, so omitted-vs-explicit-null cannot be recovered
        # here. A non-blank ``department_id`` reassigns the team; a missing /
        # null value leaves the assignment untouched (``UNSET``).
        department_id: NotBlankStr | None | UnsetType = (
            args.department_id if args.department_id is not None else UNSET
        )
        record = await team_service_of(app_state).update_team(
            team_id=args.team_id,
            actor_id=require_actor_id(actor),
            name=args.name,
            department_id=department_id,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        missing = NotFoundError(f"Team {args.team_id} not found")
        log_handler_invoke_failed(tool, missing, team_id=str(args.team_id))
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(record.to_dict())


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
        team_id = typed_args(arguments, TeamsDeleteArgs).team_id
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
            team_id=team_id,
            actor_id=actor_id,
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_ADMIN_OP_EXECUTED,
                tool_name=tool,
                actor_agent_id=actor_id,
                reason=reason,
                team_id=team_id,
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
    """List role-version snapshots, optionally filtered by role name.

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
        version_id = typed_args(arguments, RoleVersionsGetArgs).version_id
        version = await role_version_service_of(app_state).get_version(version_id)
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
        missing = NotFoundError(f"Version {version_id} not found")
        log_handler_invoke_failed(tool, missing, version_id=version_id)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(_to_jsonable(version))
