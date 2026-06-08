"""Workflow definition CRUD MCP handlers.

List / get / create / update / delete / validate for workflow
definitions, routed through the ``workflow_service`` facade on
``AppState``. ``_workflows_delete`` enforces the admin guardrail triple
and emits ``MCP_ADMIN_OP_EXECUTED`` on success.
"""

from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.workflow.service import (
    WorkflowDefinitionExistsError,
    WorkflowDefinitionNotFoundError,
    WorkflowDefinitionRevisionMismatchError,
    WorkflowService,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers.common import (
    dump_many,
    err,
    ok,
    paginate_sequence,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_id,
    coerce_pagination,
    require_arg,
    require_non_blank,
)
from synthorg.meta.mcp.handlers.common_logging import (
    log_handler_argument_invalid,
    log_handler_guardrail_violated,
    log_handler_invoke_failed,
)
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_INVOKE_FAILED,
    MCP_HANDLER_INVOKE_SUCCESS,
)

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_ARG_DEF_ID = "workflow_id"


def _service(app_state: AppState) -> WorkflowService:
    """Return the workflow service facade.

    Handlers must route through the injected ``workflow_service`` slot
    so hot-swap / lifecycle behavior flows through one canonical path.
    Callers that have not wired the service on ``AppState`` get a loud
    runtime error instead of a silent per-call construction that would
    bypass the facade.

    Returns:
        ``WorkflowService`` instance.

    Raises:
        RuntimeError: Raised on the corresponding failure path.
    """
    cached: WorkflowService | None = app_state.slice(EngineStateSlice).workflow_service
    if cached is None:
        # ``MCP_HANDLER_LAZY_SERVICE_INIT`` is a DEBUG-level telemetry
        # event for lazy-init paths.  This branch is a hard runtime
        # misconfiguration (the service is never expected to be
        # ``None`` post-bootstrap), so emit the generic invoke-failed
        # event at WARNING and then raise.
        logger.warning(
            MCP_HANDLER_INVOKE_FAILED,
            tool_name="workflows._service",
            service="workflow_service",
            reason="workflow_service_of(app_state) not wired",
        )
        msg = "workflow_service not wired on app_state"
        raise RuntimeError(msg)
    return cached


async def _workflows_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_workflows_list`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_workflows_list"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        # MCP list handlers paginate the in-memory tuple; fetch one
        # page-worth at the repository layer so unbounded scans cannot
        # be triggered from MCP. ``limit + offset`` covers the slice the
        # paginator will hand back without over-fetching.
        items = await _service(app_state).list_definitions(limit=limit + offset)
        page, meta = paginate_sequence(items, offset=offset, limit=limit)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def _workflows_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_workflows_get`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_workflows_get"
    try:
        def_id = require_non_blank(arguments, _ARG_DEF_ID)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        defn = await _service(app_state).get_definition(def_id)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if defn is None:
        missing = WorkflowDefinitionNotFoundError(
            f"Workflow definition {def_id!r} not found",
        )
        log_handler_invoke_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=defn.model_dump(mode="json"))


async def _workflows_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_workflows_create`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_workflows_create"
    try:
        definition_dict = require_arg(arguments, "definition", dict)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    from synthorg.engine.workflow.definition import (  # noqa: PLC0415
        WorkflowDefinition,
    )

    try:
        definition = WorkflowDefinition.model_validate(definition_dict)
    except ValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")

    saved_by = actor_id(actor) or "mcp"
    try:
        created = await _service(app_state).create_definition(
            definition,
            saved_by=saved_by,
        )
    except WorkflowDefinitionExistsError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="already_exists")
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=created.model_dump(mode="json"))


async def _workflows_update(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_workflows_update`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_workflows_update"
    try:
        definition_dict = require_arg(arguments, "definition", dict)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    from synthorg.engine.workflow.definition import (  # noqa: PLC0415
        WorkflowDefinition,
    )

    try:
        definition = WorkflowDefinition.model_validate(definition_dict)
    except ValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")

    saved_by = actor_id(actor) or "mcp"
    try:
        updated = await _service(app_state).update_definition(
            definition,
            saved_by=saved_by,
        )
    except WorkflowDefinitionNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except WorkflowDefinitionRevisionMismatchError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=updated.model_dump(mode="json"))


async def _workflows_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_workflows_delete`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_workflows_delete"
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
        def_id = require_non_blank(arguments, _ARG_DEF_ID)
        deleted = await _service(app_state).delete_definition(def_id)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if not deleted:
        missing = WorkflowDefinitionNotFoundError(
            f"Workflow definition {def_id!r} not found",
        )
        log_handler_invoke_failed(tool, missing)
        return err(missing, domain_code="not_found")

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=actor_id(resolved_actor),
        reason=reason,
        target_id=def_id,
    )
    return ok()


async def _workflows_validate(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_workflows_validate`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_workflows_validate"
    try:
        definition_dict = require_arg(arguments, "definition", dict)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)

    from synthorg.engine.workflow.definition import (  # noqa: PLC0415
        WorkflowDefinition,
    )

    try:
        definition = WorkflowDefinition.model_validate(definition_dict)
    except ValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")

    try:
        result = await _service(app_state).validate_definition(definition)
    except Exception as exc:
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=result.model_dump(mode="json"))
