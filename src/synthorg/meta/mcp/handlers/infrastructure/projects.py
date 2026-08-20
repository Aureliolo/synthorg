"""Project MCP handlers (infrastructure sub-domain).

These read and write the projects an operator sees. That is worth stating,
because they did not: the tools ran against a process-local dict with no
persistence behind it, so an agent listing projects saw none of the
organisation's, creating one persisted nothing, and deleting one deleted
nothing while reporting success. A tool that answers confidently about a store
nobody else can see is worse than an absent one, because an agent acts on the
answer.

Delete takes the same path the dashboard's own delete takes, cascade included,
so the children, the tombstone, the workspace tree and the board's event cannot
differ by which door the deletion came through.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID

from synthorg.api.controllers._project_removal import remove_project
from synthorg.api.services.project_service import ProjectService
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.domains._remaining_args import (
    ProjectsCreateArgs,
    ProjectsDeleteArgs,
    ProjectsGetArgs,
    ProjectsListArgs,
    ProjectsUpdateArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
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
from synthorg.observability.events.infrastructure import (
    PROJECT_CREATED_VIA_MCP,
    PROJECT_DELETED_VIA_MCP,
    PROJECT_UPDATED_VIA_MCP,
)
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_INVOKE_SUCCESS,
)
from synthorg.persistence.project_protocol import ProjectFilterSpec
from synthorg.persistence.state import persistence_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_PROJECT_ID = "project_id"
_TY_UUID = "UUID string"


def _service(app_state: AppState) -> ProjectService:
    """Build the project service over the same repository the REST route uses.

    Returns:
        A service bound to this deployment's persisted projects.
    """
    return ProjectService(repo=persistence_of(app_state).projects)


def _rendered(project: Project) -> dict[str, object]:
    """Render *project* for an MCP response.

    Returns:
        The project as JSON-ready primitives.
    """
    return project.model_dump(mode="json")


def _validated_uuid(project_id: str) -> NotBlankStr:
    """Return *project_id* once it is confirmed to be a UUID.

    Returns:
        The identifier, unchanged.

    Raises:
        ArgumentValidationError: *project_id* is not a UUID string.
    """
    try:
        UUID(project_id)
    except ValueError as uuid_exc:
        raise ArgumentValidationError(_ARG_PROJECT_ID, _TY_UUID) from uuid_exc
    return NotBlankStr(project_id)


async def _projects_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List projects (paginated).

    Returns:
        Resulting string.
    """
    tool = "synthorg_projects_list"
    try:
        page_args = typed_args(arguments, ProjectsListArgs)
        offset, limit = page_args.offset, page_args.limit
        repo = persistence_of(app_state).projects
        page = await repo.list_items(limit=limit, offset=offset)
        total = await repo.count(ProjectFilterSpec())
        pagination = PaginationMeta(total=total, offset=offset, limit=limit)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok([_rendered(p) for p in page], pagination=pagination)


async def _projects_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single project by ID.

    Returns:
        Resulting string.
    """
    tool = "synthorg_projects_get"
    try:
        project_id = _validated_uuid(typed_args(arguments, ProjectsGetArgs).project_id)
        project = await _service(app_state).get(project_id)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if project is None:
        missing = NotFoundError(f"Project {project_id} not found")
        log_handler_invoke_failed(tool, missing, project_id=project_id)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(_rendered(project))


async def _projects_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Create a new project (non-destructive write).

    Returns:
        Resulting string.
    """
    tool = "synthorg_projects_create"
    try:
        args = typed_args(arguments, ProjectsCreateArgs)
        actor_id = require_actor_id(actor)
        project = await _service(app_state).create(
            Project(name=args.name, description=args.description or "")
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(
        PROJECT_CREATED_VIA_MCP,
        project_id=str(project.id),
        actor_id=actor_id,
    )
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(_rendered(project))


async def _projects_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Update an existing project (partial patch).

    Returns:
        Resulting string.
    """
    tool = "synthorg_projects_update"
    try:
        args = typed_args(arguments, ProjectsUpdateArgs)
        project_id = _validated_uuid(args.project_id)
        actor_id = require_actor_id(actor)
        service = _service(app_state)
        current = await service.get(project_id)
        if current is None:
            missing = NotFoundError(f"Project {project_id} not found")
            log_handler_invoke_failed(tool, missing, project_id=project_id)
            return err(missing, domain_code="not_found")
        # Version-guarded on the row this patch was derived from, so a
        # concurrent operator edit is refused rather than overwritten by
        # whichever of the two wrote last.
        updated = await service.update(
            current.model_copy(
                update={
                    "name": args.name if args.name is not None else current.name,
                    "description": (
                        args.description
                        if args.description is not None
                        else current.description
                    ),
                }
            ),
            expected_version=current.version,
        )
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(
        PROJECT_UPDATED_VIA_MCP,
        project_id=project_id,
        actor_id=actor_id,
    )
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(_rendered(updated))


async def _projects_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Delete a project (destructive; enforces guardrails).

    Returns:
        Resulting string.
    """
    tool = "synthorg_projects_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        project_id = _validated_uuid(
            typed_args(arguments, ProjectsDeleteArgs).project_id
        )
        actor_id = require_actor_id(resolved_actor)
        # The dashboard's own delete, cascade and all. A second removal path
        # would be a second answer to what a deletion owes, and the one this
        # replaced settled it by taking nothing with it at all.
        #
        # No plugin: an MCP call has no request to resolve one from, so the
        # board learns of the removal on its next read rather than live.
        await remove_project(
            app_state,
            _service(app_state),
            project_id,
            requested_by=actor_id,
            channels_plugin=None,
        )
        logger.info(
            PROJECT_DELETED_VIA_MCP,
            project_id=project_id,
            actor_id=actor_id,
            reason=reason,
            removed=True,
        )
        logger.info(
            MCP_ADMIN_OP_EXECUTED,
            tool_name=tool,
            actor_agent_id=actor_id,
            reason=reason,
            project_id=project_id,
        )
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except NotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok({"removed": True})


PROJECTS_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_projects_list": _projects_list,
        "synthorg_projects_get": _projects_get,
        "synthorg_projects_create": _projects_create,
        "synthorg_projects_update": _projects_update,
        "synthorg_projects_delete": _projects_delete,
    },
)
