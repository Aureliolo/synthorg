"""Workflow execution MCP handlers.

Split out of ``meta/mcp/handlers/workflows.py`` so the parent module
stays under the project's 800-line ceiling. The four execution handlers
(list / get / start / cancel) share the same envelope contract,
guardrails, and error mapping as the rest of the workflow domain;
they just have enough error branches that grouping them with the rest
pushed the parent module past budget.
"""

import copy
from typing import TYPE_CHECKING

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.errors import (
    SubworkflowDepthExceededError,
    WorkflowDefinitionInvalidError,
    WorkflowExecutionAlreadyTerminalError,
    WorkflowExecutionError,
    WorkflowExecutionNotFoundError,
)
from synthorg.engine.state import EngineStateSlice, workflow_execution_service_of
from synthorg.engine.workflow.execution_service import (
    WorkflowExecutionService,
)
from synthorg.meta.mcp.domains._workflows_org_args import (
    WorkflowExecutionsCancelArgs,
    WorkflowExecutionsGetArgs,
    WorkflowExecutionsListArgs,
    WorkflowExecutionsStartArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    capability_gap,
    dump_many,
    err,
    ok,
    paginate_sequence,
    require_admin_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import (
    actor_id,
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

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


_WHY_EXECUTION_SERVICE = (
    "workflow_execution_service is not wired on app_state in this deployment"
)


def _execution_service(app_state: AppState) -> WorkflowExecutionService | None:
    # The concrete ``AppState.workflow_execution_service`` property
    # raises ``ServiceUnavailableError`` when the slot is empty, so we
    # gate on the ``has_<service>`` predicate first instead of relying
    # on ``getattr(..., default)`` -- which only catches
    # ``AttributeError`` and would otherwise let the property's exception
    # short-circuit the handler before it could return ``capability_gap``.
    """Return execution service."""
    if app_state.slice(EngineStateSlice).workflow_execution_service is None:
        return None
    return workflow_execution_service_of(app_state)


async def workflow_executions_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """List executions, optionally filtered by workflow definition and status.

    Returns:
        Resulting string.
    """
    tool = "synthorg_workflow_executions_list"
    service = _execution_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_EXECUTION_SERVICE)
    try:
        args = typed_args(arguments, WorkflowExecutionsListArgs)
        offset, limit = args.offset, args.limit
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        # MCP list handlers paginate in-memory; fetch one page-worth
        # at the repository layer so unbounded scans cannot be
        # triggered from MCP.
        executions = await service.list_executions(
            args.workflow_id,
            status=args.status,
            limit=limit + offset,
        )
        page, meta = paginate_sequence(executions, offset=offset, limit=limit)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def workflow_executions_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Fetch a single workflow execution by id.

    Returns:
        Resulting string.
    """
    tool = "synthorg_workflow_executions_get"
    service = _execution_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_EXECUTION_SERVICE)
    try:
        execution_id = typed_args(arguments, WorkflowExecutionsGetArgs).execution_id
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        execution = await service.get_execution(execution_id)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    if execution is None:
        missing = WorkflowExecutionNotFoundError(
            f"Workflow execution {execution_id!r} not found",
        )
        log_handler_invoke_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=execution.model_dump(mode="json"))


async def workflow_executions_start(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Activate a workflow definition (alias: start an execution).

    Returns:
        Resulting string.
    """
    tool = "synthorg_workflow_executions_start"
    service = _execution_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_EXECUTION_SERVICE)
    try:
        args = typed_args(arguments, WorkflowExecutionsStartArgs)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    activated_by = actor_id(actor) or "mcp"
    # Deep-copy ``context`` at the handler boundary so downstream service
    # code that scrubs / annotates / sorts the context cannot leak back
    # into the caller-owned request state.
    try:
        execution = await service.activate(
            args.workflow_id,
            project=args.project,
            activated_by=activated_by,
            context=copy.deepcopy(args.context),
        )
    except WorkflowExecutionNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except (WorkflowDefinitionInvalidError, SubworkflowDepthExceededError) as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="invalid_argument")
    except WorkflowExecutionError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=execution.model_dump(mode="json"))


async def workflow_executions_cancel(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Cancel a running workflow execution (destructive).

    Returns:
        Resulting string.
    """
    tool = "synthorg_workflow_executions_cancel"
    # Run the destructive-op triple BEFORE argument parsing so anonymous
    # or unconfirmed callers see the guardrail violation first (the
    # contract every other admin_tool surfaces) instead of an
    # ``invalid_argument`` envelope when ``execution_id`` is missing.
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    # Check service availability before parsing ``execution_id`` so
    # deployments without ``workflow_execution_service`` surface
    # ``capability_gap`` even when the caller's input is malformed,
    # matching the order the other three execution handlers use.
    service = _execution_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_EXECUTION_SERVICE)
    try:
        execution_id = typed_args(arguments, WorkflowExecutionsCancelArgs).execution_id
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    cancelled_by = actor_id(resolved_actor) or "mcp"
    try:
        execution = await service.cancel_execution(
            execution_id,
            cancelled_by=cancelled_by,
        )
    except WorkflowExecutionNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except WorkflowExecutionAlreadyTerminalError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=cancelled_by,
        reason=reason,
        target_id=execution_id,
    )
    return ok(data=execution.model_dump(mode="json"))


__all__ = [
    "workflow_executions_cancel",
    "workflow_executions_get",
    "workflow_executions_list",
    "workflow_executions_start",
]
