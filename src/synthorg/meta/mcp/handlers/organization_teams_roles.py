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
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.domains._workflows_org_args import TeamsGetArgs, TeamsListArgs
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import (
    _map_capability,
    _require_str,
    _require_uuid,
    _to_jsonable,
    typed_args,
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    err,
    ok,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    get_optional_str,
    require_actor_id,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED
from synthorg.organization.services import UNSET, UnsetType
from synthorg.organization.state import role_version_service_of, team_service_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_TEAM_ID = "team_id"
_TY_UUID = "UUID string"


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
    try:
        page_args = typed_args(arguments, TeamsListArgs)
        offset, limit = page_args.offset, page_args.limit
        page, total = await team_service_of(app_state).list_teams(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([t.to_dict() for t in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)


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
        return err(
            LookupError(f"Team {team_id} not found"),
            domain_code="not_found",
        )
    return ok(record.to_dict())


# lint-allow: handler-arguments-get -- cataloged mismatch: handler reads an
# optional `department_id`, but TeamsCreateArgs declares a required `department`.
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
    try:
        name = _require_str(arguments, "name")
        department_id = get_optional_str(arguments, "department_id")
        record = await team_service_of(app_state).create_team(
            name=name,
            actor_id=require_actor_id(actor),
            department_id=department_id,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(record.to_dict())


# lint-allow: handler-arguments-get -- cataloged mismatch: handler reads
# name/department_id (with an UNSET sentinel), but TeamsUpdateArgs declares an
# opaque `updates` dict.
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
    try:
        team_id = _require_uuid(arguments, "team_id")
        name = get_optional_str(arguments, "name")
        if "department_id" in arguments:
            department_id: NotBlankStr | None | UnsetType = get_optional_str(
                arguments,
                "department_id",
            )
        else:
            department_id = UNSET
        record = await team_service_of(app_state).update_team(
            team_id=team_id,
            actor_id=require_actor_id(actor),
            name=name,
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
        return err(
            LookupError(f"Team {team_id} not found"),
            domain_code="not_found",
        )
    return ok(record.to_dict())


# lint-allow: handler-arguments-get -- cataloged mismatch: handler enforces
# require_admin_guardrails but TeamsDeleteArgs (write_tool, not admin_tool)
# declares no AdminGuardrailFields.
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
        team_id = _require_uuid(arguments, "team_id")
        actor_id = require_actor_id(resolved_actor)
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
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({"removed": removed})


# lint-allow: handler-arguments-get -- cataloged mismatch: handler reads an
# optional `role_name` and no pagination, but RoleVersionsListArgs declares a
# required `role_name` plus PaginationFields the handler never forwards.
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
    try:
        role_name = get_optional_str(arguments, "role_name")
        versions = await role_version_service_of(app_state).list_versions(
            role_name=role_name,
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
    return ok([_to_jsonable(v) for v in versions])


# lint-allow: handler-arguments-get -- cataloged mismatch: handler reads a
# string `version_id`, but RoleVersionsGetArgs declares role_name + int version_num.
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
    try:
        version_id = _require_str(arguments, "version_id")
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
        return err(
            LookupError(f"Version {version_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(version))
