"""Workflow version-history MCP handlers.

List / get for workflow definition version snapshots, routed through
the ``workflow_version_service`` facade on ``AppState``. Each handler
degrades to a ``capability_gap`` envelope when the service is not wired.
"""

from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import (
    EngineStateSlice,
    workflow_version_service_of,
)
from synthorg.engine.workflow.service import WorkflowDefinitionNotFoundError
from synthorg.engine.workflow.version_service import WorkflowVersionService
from synthorg.meta.mcp.errors import ArgumentValidationError
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    capability_gap,
    dump_many,
    err,
    ok,
)
from synthorg.meta.mcp.handlers.common_args import (
    coerce_pagination,
    require_non_blank,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_HANDLER_INVOKE_SUCCESS

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_TY_INT = "integer"
_ARG_DEF_ID = "workflow_id"
_ARG_REVISION = "revision"

_WHY_VERSION_SERVICE = (
    "workflow_version_service is not wired on app_state in this deployment"
)


def _version_service(app_state: AppState) -> WorkflowVersionService | None:
    """Return the wired version service, or ``None`` to trigger gap.

    The same ``has_<service>`` predicate guards the call site so the
    ``AppState`` property's ``ServiceUnavailableError`` never escapes
    past the ``capability_gap`` fallback.

    Returns:
        The ``WorkflowVersionService`` value when present, ``None`` otherwise.
    """
    if app_state.slice(EngineStateSlice).workflow_version_service is None:
        return None
    return workflow_version_service_of(app_state)


def _require_int(
    arguments: dict[str, object],
    key: str,
    *,
    positive: bool = False,
) -> int:
    """Extract an integer argument or raise ``ArgumentValidationError``.

    Booleans are explicitly rejected because ``isinstance(True, int)``
    is ``True`` in Python; ``positive=True`` additionally rejects
    non-positive values so callers like ``_workflow_versions_get``
    (where the service requires ``revision >= 1``) get the more
    accurate ``invalid_argument`` envelope here instead of bouncing off
    a deeper validation layer.

    Returns:
        Resulting integer.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    raw = arguments.get(key)
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ArgumentValidationError(key, _TY_INT)
    if positive and raw < 1:
        raise ArgumentValidationError(key, _TY_INT)
    return raw


async def _workflow_versions_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_workflow_versions_list`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_workflow_versions_list"
    service = _version_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_VERSION_SERVICE)
    try:
        def_id = require_non_blank(arguments, _ARG_DEF_ID)
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        page, total = await service.list_versions(
            NotBlankStr(def_id),
            offset=offset,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def _workflow_versions_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_workflow_versions_get`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_workflow_versions_get"
    service = _version_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_VERSION_SERVICE)
    try:
        def_id = require_non_blank(arguments, _ARG_DEF_ID)
        revision = _require_int(arguments, _ARG_REVISION, positive=True)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        snapshot = await service.get_version(NotBlankStr(def_id), revision)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if snapshot is None:
        missing = WorkflowDefinitionNotFoundError(
            f"Workflow definition {def_id!r} revision {revision!r} not found",
        )
        log_handler_invoke_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=snapshot.model_dump(mode="json"))
