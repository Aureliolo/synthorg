"""Subworkflow MCP handlers.

List / get / create / delete for reusable subworkflow definitions,
routed through the ``subworkflow_service`` facade on ``AppState``.
Each handler degrades to a ``capability_gap`` envelope when the service
is not wired; ``_subworkflows_delete`` enforces the admin guardrail
triple and emits ``MCP_ADMIN_OP_EXECUTED`` on success.
"""

from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.errors import (
    SubworkflowIOError,
    SubworkflowNotFoundError,
)
from synthorg.engine.state import (
    EngineStateSlice,
    subworkflow_service_of,
)
from synthorg.engine.workflow.subworkflow_service import (
    SubworkflowHasParentsError,
    SubworkflowService,
)
from synthorg.meta.mcp.domains._workflows_org_args import (
    SubworkflowsCreateArgs,
    SubworkflowsDeleteArgs,
    SubworkflowsGetArgs,
    SubworkflowsListArgs,
)
from synthorg.meta.mcp.errors import (
    ArgumentValidationError,
    GuardrailViolationError,
)
from synthorg.meta.mcp.handlers._mcp_handler_common import typed_args
from synthorg.meta.mcp.handlers.common import (
    PaginationMeta,
    capability_gap,
    dump_many,
    err,
    ok,
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

_WHY_SUBWORKFLOW_SERVICE = (
    "subworkflow_service is not wired on app_state in this deployment"
)


def _subworkflow_service(app_state: AppState) -> SubworkflowService | None:
    """Return the wired subworkflow service, or ``None`` to trigger gap.

    Gates on ``has_subworkflow_service`` first because the
    ``AppState.subworkflow_service`` property raises
    ``ServiceUnavailableError`` when the slot is empty -- ``getattr``
    only catches ``AttributeError`` and would otherwise let the
    property's exception escape past the ``capability_gap`` fallback.

    Returns:
        The ``SubworkflowService`` value when present, ``None`` otherwise.
    """
    if app_state.slice(EngineStateSlice).subworkflow_service is None:
        return None
    return subworkflow_service_of(app_state)


async def _subworkflows_list(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_subworkflows_list`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    tool = "synthorg_subworkflows_list"
    service = _subworkflow_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_SUBWORKFLOW_SERVICE)
    try:
        args = typed_args(arguments, SubworkflowsListArgs)
        offset, limit = args.offset, args.limit
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        page, total = await service.list_summaries(
            offset=offset,
            limit=limit,
            query=args.query,
        )
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def _subworkflows_get(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    """Handle the ``synthorg_subworkflows_get`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.

    Raises:
        ArgumentValidationError: Raised on the corresponding failure path.
    """
    tool = "synthorg_subworkflows_get"
    service = _subworkflow_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_SUBWORKFLOW_SERVICE)
    try:
        args = typed_args(arguments, SubworkflowsGetArgs)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    try:
        defn = await service.get(args.subworkflow_id, args.version)
    except SubworkflowNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=defn.model_dump(mode="json"))


async def _subworkflows_create(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_subworkflows_create`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_subworkflows_create"
    service = _subworkflow_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_SUBWORKFLOW_SERVICE)
    try:
        definition_dict = typed_args(arguments, SubworkflowsCreateArgs).definition
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
        created = await service.create(definition, saved_by=saved_by)
    except SubworkflowIOError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="invalid_argument")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=created.model_dump(mode="json"))


async def _subworkflows_delete(
    *,
    app_state: AppState,
    arguments: dict[str, object],
    actor: AgentIdentity | None = None,
) -> str:
    """Handle the ``synthorg_subworkflows_delete`` MCP tool.

    Returns:
        JSON-encoded MCP envelope string.
    """
    tool = "synthorg_subworkflows_delete"
    # Run the destructive-op guardrails first so the standard
    # parametrised destructive-op test sweep (which does not seed
    # ``version``) sees the guardrail violation before any field
    # validation.  Field-level validation runs after the guardrail.
    try:
        reason, resolved_actor = require_admin_guardrails(arguments, actor)
    except GuardrailViolationError as exc:
        log_handler_guardrail_violated(tool, exc)
        return err(exc)
    try:
        args = typed_args(arguments, SubworkflowsDeleteArgs)
    except ArgumentValidationError as exc:
        log_handler_argument_invalid(tool, exc)
        return err(exc)
    sub_id = args.subworkflow_id
    version = args.version
    service = _subworkflow_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_SUBWORKFLOW_SERVICE)
    deleted_by = actor_id(resolved_actor) or "mcp"
    try:
        await service.delete(
            sub_id,
            version,
            reason=reason,
            actor_id=deleted_by,
        )
    except SubworkflowHasParentsError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except SubworkflowNotFoundError as exc:
        log_handler_invoke_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except Exception as exc:  # noqa: BLE001 -- mcp tool boundary
        reraise_critical(exc)
        log_handler_invoke_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_ADMIN_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=deleted_by,
        reason=reason,
        target_id=f"{sub_id}@{version}",
    )
    return ok()
