"""Organization domain MCP handlers.

19 tools across company, departments, teams, and role-version history.
Each handler shims through the corresponding facade on
:class:`AppState`; operations whose underlying primitive cannot satisfy
them surface :class:`CapabilityNotSupportedError` -> typed
``not_supported`` envelope.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
    invalid_argument,
)
from synthorg.meta.mcp.handler_protocol import (
    ToolHandler,  # noqa: TC001 -- PEP 649 annotation
)
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    err,
    ok,
    require_destructive_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_label,
    coerce_pagination,
    get_optional_str,
    require_arg,
    require_dict,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_DESTRUCTIVE_OP_EXECUTED,
    MCP_HANDLER_CAPABILITY_GAP,
)
from synthorg.organization.services import UNSET, UnsetType

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)

_TY_STRING = "non-blank string"
_TY_UUID = "UUID string"
_TY_DICT = "mapping of str -> object"
_TY_LIST = "sequence of strings"


def _map_capability(tool: str, exc: CapabilityNotSupportedError) -> str:
    """Translate a facade-side capability gap into a typed error envelope.

    Emits :data:`MCP_HANDLER_CAPABILITY_GAP` so capability telemetry is
    distinct from invoke failures.
    """
    logger.info(
        MCP_HANDLER_CAPABILITY_GAP,
        tool_name=tool,
        capability=exc.capability,
    )
    return err(exc, domain_code=exc.domain_code)


def _require_str(arguments: dict[str, Any], key: str) -> NotBlankStr:
    """Extract a required non-blank string or raise ``ArgumentValidationError``."""
    value = get_optional_str(arguments, key)
    if value is None:
        raise invalid_argument(key, _TY_STRING)
    return value


def _require_uuid(arguments: dict[str, Any], key: str) -> NotBlankStr:
    """Extract a required UUID-shaped string or raise ``ArgumentValidationError``."""
    value = require_arg(arguments, key, str)
    try:
        UUID(value)
    except ValueError as exc:
        raise invalid_argument(key, _TY_UUID) from exc
    return NotBlankStr(value)


def _require_str_list(arguments: dict[str, Any], key: str) -> tuple[str, ...]:
    """Extract a required sequence of non-blank strings, or raise on error."""
    raw = arguments.get(key)
    if not isinstance(raw, (list, tuple)):
        raise invalid_argument(key, _TY_LIST)
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise invalid_argument(key, _TY_LIST)
    return tuple(raw)


def _require_uuid_list(
    arguments: dict[str, Any],
    key: str,
) -> tuple[NotBlankStr, ...]:
    """Extract a required sequence of UUID-shaped strings.

    Each entry is validated with :func:`UUID` so malformed IDs never
    reach the mutation service.
    """
    entries = _require_str_list(arguments, key)
    for entry in entries:
        try:
            UUID(entry)
        except ValueError as exc:
            raise invalid_argument(key, _TY_UUID) from exc
    return tuple(NotBlankStr(e) for e in entries)


def _to_jsonable(value: Any) -> Any:
    """Coerce a Pydantic / ``to_dict`` value into a JSON-serialisable form."""
    dump_fn = getattr(value, "model_dump", None)
    if callable(dump_fn):
        return dump_fn(mode="json")
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return value


# ── company ─────────────────────────────────────────────────────────


async def _company_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return the current company record."""
    tool = "synthorg_company_get"
    try:
        company = await app_state.company_read_service.get_company()
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(_to_jsonable(company))


async def _company_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Apply a payload patch to the company record (non-destructive write)."""
    tool = "synthorg_company_update"
    try:
        payload = require_dict(arguments, "payload")
        result = await app_state.company_read_service.update_company(
            payload=payload,
            actor_id=actor_label(actor),
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(_to_jsonable(result))


async def _company_list_departments(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List every department attached to the company."""
    tool = "synthorg_company_list_departments"
    try:
        departments = await app_state.company_read_service.list_departments()
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(d) for d in departments])


async def _company_reorder_departments(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Replace the department display order with the supplied sequence."""
    tool = "synthorg_company_reorder_departments"
    try:
        ids = _require_uuid_list(arguments, "department_ids")
        await app_state.company_read_service.reorder_departments(
            department_ids=ids,
            actor_id=actor_label(actor),
        )
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(None)


async def _company_versions_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],  # noqa: ARG001
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List every snapshot in the company version history."""
    tool = "synthorg_company_versions_list"
    try:
        versions = await app_state.company_read_service.list_versions()
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(v) for v in versions])


async def _company_versions_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single company version snapshot by ID."""
    tool = "synthorg_company_versions_get"
    try:
        version_id = _require_str(arguments, "version_id")
        version = await app_state.company_read_service.get_version(version_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if version is None:
        return err(
            LookupError(f"Version {version_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(version))


# ── departments ─────────────────────────────────────────────────────


async def _departments_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return a paginated slice of departments."""
    tool = "synthorg_departments_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await app_state.department_service.list_departments(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([d.to_dict() for d in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _departments_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single department by UUID."""
    tool = "synthorg_departments_get"
    try:
        department_id = _require_uuid(arguments, "department_id")
        record = await app_state.department_service.get_department(department_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        return err(
            LookupError(f"Department {department_id} not found"),
            domain_code="not_found",
        )
    return ok(record.to_dict())


async def _departments_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new department record (non-destructive write)."""
    tool = "synthorg_departments_create"
    try:
        name = _require_str(arguments, "name")
        description = _require_str(arguments, "description")
        record = await app_state.department_service.create_department(
            name=name,
            description=description,
            actor_id=actor_label(actor),
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(record.to_dict())


async def _departments_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Update name / description on an existing department (partial patch)."""
    tool = "synthorg_departments_update"
    try:
        department_id = _require_uuid(arguments, "department_id")
        name = get_optional_str(arguments, "name")
        description = get_optional_str(arguments, "description")
        record = await app_state.department_service.update_department(
            department_id=department_id,
            actor_id=actor_label(actor),
            name=name,
            description=description,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        return err(
            LookupError(f"Department {department_id} not found"),
            domain_code="not_found",
        )
    return ok(record.to_dict())


async def _departments_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a department (destructive; enforces confirm + reason + actor)."""
    tool = "synthorg_departments_delete"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        department_id = _require_uuid(arguments, "department_id")
        removed = await app_state.department_service.delete_department(
            department_id=department_id,
            actor_id=actor_label(resolved_actor),
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_DESTRUCTIVE_OP_EXECUTED,
                tool_name=tool,
                actor=actor_label(resolved_actor),
                reason=reason,
                department_id=department_id,
                removed=removed,
            )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({"removed": removed})


async def _departments_get_health(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return a lightweight health summary for a single department."""
    tool = "synthorg_departments_get_health"
    try:
        department_id = _require_uuid(arguments, "department_id")
        result = await app_state.department_service.get_health(department_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(dict(result))


# ── teams ───────────────────────────────────────────────────────────


async def _teams_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Return a paginated slice of teams."""
    tool = "synthorg_teams_list"
    try:
        offset, limit = coerce_pagination(arguments)
        page, total = await app_state.team_service.list_teams(
            offset=offset,
            limit=limit,
        )
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
        return ok([t.to_dict() for t in page], pagination=pagination)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)


async def _teams_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single team by UUID."""
    tool = "synthorg_teams_get"
    try:
        team_id = _require_uuid(arguments, "team_id")
        record = await app_state.team_service.get_team(team_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        return err(
            LookupError(f"Team {team_id} not found"),
            domain_code="not_found",
        )
    return ok(record.to_dict())


async def _teams_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new team record (non-destructive write)."""
    tool = "synthorg_teams_create"
    try:
        name = _require_str(arguments, "name")
        department_id = get_optional_str(arguments, "department_id")
        record = await app_state.team_service.create_team(
            name=name,
            actor_id=actor_label(actor),
            department_id=department_id,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok(record.to_dict())


async def _teams_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Update name / department on an existing team (partial patch)."""
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
        record = await app_state.team_service.update_team(
            team_id=team_id,
            actor_id=actor_label(actor),
            name=name,
            department_id=department_id,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if record is None:
        return err(
            LookupError(f"Team {team_id} not found"),
            domain_code="not_found",
        )
    return ok(record.to_dict())


async def _teams_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a team (destructive; enforces confirm + reason + actor)."""
    tool = "synthorg_teams_delete"
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
        team_id = _require_uuid(arguments, "team_id")
        removed = await app_state.team_service.delete_team(
            team_id=team_id,
            actor_id=actor_label(resolved_actor),
            reason=reason,
        )
        if removed:
            logger.info(
                MCP_DESTRUCTIVE_OP_EXECUTED,
                tool_name=tool,
                actor=actor_label(resolved_actor),
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
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok({"removed": removed})


# ── role versions ──────────────────────────────────────────────────


async def _role_versions_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List role-version snapshots, optionally filtered by role name."""
    tool = "synthorg_role_versions_list"
    try:
        role_name = get_optional_str(arguments, "role_name")
        versions = await app_state.role_version_service.list_versions(
            role_name=role_name,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    return ok([_to_jsonable(v) for v in versions])


async def _role_versions_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single role-version snapshot by ID."""
    tool = "synthorg_role_versions_get"
    try:
        version_id = _require_str(arguments, "version_id")
        version = await app_state.role_version_service.get_version(version_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except CapabilityNotSupportedError as exc:
        return _map_capability(tool, exc)
    except Exception as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if version is None:
        return err(
            LookupError(f"Version {version_id} not found"),
            domain_code="not_found",
        )
    return ok(_to_jsonable(version))


# ── dispatch table ─────────────────────────────────────────────────


ORGANIZATION_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_company_get": _company_get,
        "synthorg_company_update": _company_update,
        "synthorg_company_list_departments": _company_list_departments,
        "synthorg_company_reorder_departments": _company_reorder_departments,
        "synthorg_company_versions_list": _company_versions_list,
        "synthorg_company_versions_get": _company_versions_get,
        "synthorg_departments_list": _departments_list,
        "synthorg_departments_get": _departments_get,
        "synthorg_departments_create": _departments_create,
        "synthorg_departments_update": _departments_update,
        "synthorg_departments_delete": _departments_delete,
        "synthorg_departments_get_health": _departments_get_health,
        "synthorg_teams_list": _teams_list,
        "synthorg_teams_get": _teams_get,
        "synthorg_teams_create": _teams_create,
        "synthorg_teams_update": _teams_update,
        "synthorg_teams_delete": _teams_delete,
        "synthorg_role_versions_list": _role_versions_list,
        "synthorg_role_versions_get": _role_versions_get,
    },
)
