"""Workflow domain MCP handlers.

16 tools spanning workflow definitions, subworkflows, executions, and
versions. Every tool routes through a service facade on AppState
(``workflow_service``, ``subworkflow_service``,
``workflow_execution_service``, ``workflow_version_service``); when a
service is not wired the handler returns a ``capability_gap`` envelope
identifying the missing facade.

Destructive ops -- ``workflows_delete``, ``subworkflows_delete``, and
``workflow_executions_cancel`` -- enforce the full guardrail
(``confirm=True`` + non-blank ``reason`` + non-``None`` ``actor``) and
emit ``MCP_DESTRUCTIVE_OP_EXECUTED`` on success.
"""

import copy
from collections.abc import Mapping  # noqa: TC003 -- PEP 649 annotation
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    SubworkflowIOError,
    SubworkflowNotFoundError,
)
from synthorg.engine.workflow.service import (
    WorkflowDefinitionExistsError,
    WorkflowDefinitionNotFoundError,
    WorkflowDefinitionRevisionMismatchError,
    WorkflowService,
)
from synthorg.engine.workflow.subworkflow_service import (
    SubworkflowHasParentsError,
    SubworkflowService,
)
from synthorg.engine.workflow.version_service import (
    WorkflowVersionService,  # noqa: TC001 -- runtime annotation in helper
)
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
    capability_gap,
    dump_many,
    err,
    ok,
    paginate_sequence,
    require_destructive_guardrails,
)
from synthorg.meta.mcp.handlers.common_args import coerce_pagination, require_arg
from synthorg.meta.mcp.handlers.workflow_executions import (
    workflow_executions_cancel as workflow_executions_cancel_impl,
)
from synthorg.meta.mcp.handlers.workflow_executions import (
    workflow_executions_get as workflow_executions_get_impl,
)
from synthorg.meta.mcp.handlers.workflow_executions import (
    workflow_executions_list as workflow_executions_list_impl,
)
from synthorg.meta.mcp.handlers.workflow_executions import (
    workflow_executions_start as workflow_executions_start_impl,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_DESTRUCTIVE_OP_EXECUTED,
    MCP_HANDLER_ARGUMENT_INVALID,
    MCP_HANDLER_GUARDRAIL_VIOLATED,
    MCP_HANDLER_INVOKE_FAILED,
    MCP_HANDLER_INVOKE_SUCCESS,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity

logger = get_logger(__name__)


_TY_NON_BLANK = "non-blank string"
_TY_INT = "integer"
_ARG_DEF_ID = "workflow_id"
_ARG_SUB_ID = "subworkflow_id"
_ARG_VERSION = "version"
_ARG_REVISION = "revision"


_WHY_SUBWORKFLOW_SERVICE = (
    "subworkflow_service is not wired on app_state in this deployment"
)
_WHY_VERSION_SERVICE = (
    "workflow_version_service is not wired on app_state in this deployment"
)


def _log_invalid(tool: str, exc: Exception) -> None:
    logger.warning(
        MCP_HANDLER_ARGUMENT_INVALID,
        tool_name=tool,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


def _log_failed(tool: str, exc: Exception) -> None:
    logger.warning(
        MCP_HANDLER_INVOKE_FAILED,
        tool_name=tool,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


def _log_guardrail(tool: str, exc: GuardrailViolationError) -> None:
    logger.warning(
        MCP_HANDLER_GUARDRAIL_VIOLATED,
        tool_name=tool,
        violation=exc.violation,
    )


def _actor_id(actor: Any) -> str | None:
    """Return a stable audit identifier for ``actor`` (prefers ``.id``)."""
    if actor is None:
        return None
    agent_id = getattr(actor, "id", None)
    if agent_id is not None:
        return str(agent_id)
    name = getattr(actor, "name", None)
    return name if isinstance(name, str) and name else None


def _require_non_blank(arguments: dict[str, Any], key: str) -> str:
    """Extract a non-blank string argument, stripped of surrounding whitespace."""
    raw = arguments.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise invalid_argument(key, _TY_NON_BLANK)
    return raw.strip()


def _require_int(
    arguments: dict[str, Any],
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
    """
    raw = arguments.get(key)
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise invalid_argument(key, _TY_INT)
    if positive and raw < 1:
        raise invalid_argument(key, _TY_INT)
    return raw


def _subworkflow_service(app_state: Any) -> SubworkflowService | None:
    """Return the wired subworkflow service, or ``None`` to trigger gap.

    Gates on ``has_subworkflow_service`` first because the
    ``AppState.subworkflow_service`` property raises
    ``ServiceUnavailableError`` when the slot is empty -- ``getattr``
    only catches ``AttributeError`` and would otherwise let the
    property's exception escape past the ``capability_gap`` fallback.
    """
    if not getattr(app_state, "has_subworkflow_service", False):
        return None
    return app_state.subworkflow_service  # type: ignore[no-any-return]


def _version_service(app_state: Any) -> WorkflowVersionService | None:
    """Return the wired version service, or ``None`` to trigger gap.

    See :func:`_subworkflow_service` for the rationale -- the same
    ``has_<service>`` predicate guards the call site.
    """
    if not getattr(app_state, "has_workflow_version_service", False):
        return None
    return app_state.workflow_version_service  # type: ignore[no-any-return]


def _service(app_state: Any) -> WorkflowService:
    """Return the workflow service facade.

    Handlers must route through the injected ``workflow_service`` slot
    so hot-swap / lifecycle behavior flows through one canonical path.
    Callers that have not wired the service on ``AppState`` get a loud
    runtime error instead of a silent per-call construction that would
    bypass the facade.
    """
    cached: WorkflowService | None = getattr(app_state, "workflow_service", None)
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
            reason="app_state.workflow_service not wired",
        )
        msg = "workflow_service not wired on app_state"
        raise RuntimeError(msg)
    return cached


# --- workflow definition CRUD ---------------------------------------------


async def _workflows_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_workflows_list"
    try:
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        items = await _service(app_state).list_definitions()
        page, meta = paginate_sequence(items, offset=offset, limit=limit)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def _workflows_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_workflows_get"
    try:
        def_id = _require_non_blank(arguments, _ARG_DEF_ID)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        defn = await _service(app_state).get_definition(def_id)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    if defn is None:
        missing = WorkflowDefinitionNotFoundError(
            f"Workflow definition {def_id!r} not found",
        )
        _log_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=defn.model_dump(mode="json"))


async def _workflows_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool = "synthorg_workflows_create"
    try:
        definition_dict = require_arg(arguments, "definition", dict)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)

    from synthorg.engine.workflow.definition import (  # noqa: PLC0415
        WorkflowDefinition,
    )

    try:
        definition = WorkflowDefinition.model_validate(definition_dict)
    except ValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")

    saved_by = _actor_id(actor) or "mcp"
    try:
        created = await _service(app_state).create_definition(
            definition,
            saved_by=saved_by,
        )
    except WorkflowDefinitionExistsError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="already_exists")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=created.model_dump(mode="json"))


async def _workflows_update(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool = "synthorg_workflows_update"
    try:
        definition_dict = require_arg(arguments, "definition", dict)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)

    from synthorg.engine.workflow.definition import (  # noqa: PLC0415
        WorkflowDefinition,
    )

    try:
        definition = WorkflowDefinition.model_validate(definition_dict)
    except ValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")

    saved_by = _actor_id(actor) or "mcp"
    try:
        updated = await _service(app_state).update_definition(
            definition,
            saved_by=saved_by,
        )
    except WorkflowDefinitionNotFoundError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except WorkflowDefinitionRevisionMismatchError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=updated.model_dump(mode="json"))


async def _workflows_delete(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool = "synthorg_workflows_delete"
    try:
        def_id = _require_non_blank(arguments, _ARG_DEF_ID)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        reason, _ = require_destructive_guardrails(arguments, actor)
    except GuardrailViolationError as exc:
        _log_guardrail(tool, exc)
        return err(exc)

    try:
        deleted = await _service(app_state).delete_definition(def_id)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    if not deleted:
        missing = WorkflowDefinitionNotFoundError(
            f"Workflow definition {def_id!r} not found",
        )
        _log_failed(tool, missing)
        return err(missing, domain_code="not_found")

    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_DESTRUCTIVE_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=_actor_id(actor),
        reason=reason,
        target_id=def_id,
    )
    return ok()


async def _workflows_validate(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_workflows_validate"
    try:
        definition_dict = require_arg(arguments, "definition", dict)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)

    from synthorg.engine.workflow.definition import (  # noqa: PLC0415
        WorkflowDefinition,
    )

    try:
        definition = WorkflowDefinition.model_validate(definition_dict)
    except ValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")

    try:
        result = await _service(app_state).validate_definition(definition)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=result.model_dump(mode="json"))


# --- subworkflows ---------------------------------------------------------


async def _subworkflows_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_subworkflows_list"
    service = _subworkflow_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_SUBWORKFLOW_SERVICE)
    try:
        offset, limit = coerce_pagination(arguments)
        arg_query = "query"
        query_raw = arguments.get(arg_query)
        if query_raw is not None and not isinstance(query_raw, str):
            raise invalid_argument(arg_query, _TY_NON_BLANK)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        page, total = await service.list_summaries(
            offset=offset,
            limit=limit,
            query=query_raw,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def _subworkflows_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_subworkflows_get"
    service = _subworkflow_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_SUBWORKFLOW_SERVICE)
    try:
        sub_id = _require_non_blank(arguments, _ARG_SUB_ID)
        version_raw = arguments.get(_ARG_VERSION)
        if version_raw is not None and (
            not isinstance(version_raw, str) or not version_raw.strip()
        ):
            raise invalid_argument(_ARG_VERSION, _TY_NON_BLANK)
        version = NotBlankStr(version_raw.strip()) if version_raw is not None else None
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        defn = await service.get(NotBlankStr(sub_id), version)
    except SubworkflowNotFoundError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=defn.model_dump(mode="json"))


async def _subworkflows_create(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool = "synthorg_subworkflows_create"
    service = _subworkflow_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_SUBWORKFLOW_SERVICE)
    try:
        definition_dict = require_arg(arguments, "definition", dict)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)

    from synthorg.engine.workflow.definition import (  # noqa: PLC0415
        WorkflowDefinition,
    )

    try:
        definition = WorkflowDefinition.model_validate(definition_dict)
    except ValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc, domain_code="invalid_argument")

    saved_by = _actor_id(actor) or "mcp"
    try:
        created = await service.create(definition, saved_by=saved_by)
    except SubworkflowIOError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="invalid_argument")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=created.model_dump(mode="json"))


async def _subworkflows_delete(  # noqa: PLR0911 -- error mapping fans out
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,
) -> str:
    tool = "synthorg_subworkflows_delete"
    # Run the destructive-op guardrails first so the standard
    # parametrised destructive-op test sweep (which does not seed
    # ``version``) sees the guardrail violation before any field
    # validation.  Field-level validation runs after the guardrail.
    try:
        reason, resolved_actor = require_destructive_guardrails(arguments, actor)
    except GuardrailViolationError as exc:
        _log_guardrail(tool, exc)
        return err(exc)
    try:
        sub_id = _require_non_blank(arguments, _ARG_SUB_ID)
        version = _require_non_blank(arguments, _ARG_VERSION)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    service = _subworkflow_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_SUBWORKFLOW_SERVICE)
    deleted_by = _actor_id(resolved_actor) or "mcp"
    try:
        await service.delete(
            NotBlankStr(sub_id),
            NotBlankStr(version),
            reason=reason,
            actor_id=deleted_by,
        )
    except SubworkflowHasParentsError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="conflict")
    except SubworkflowNotFoundError as exc:
        _log_failed(tool, exc)
        return err(exc, domain_code="not_found")
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    logger.info(
        MCP_DESTRUCTIVE_OP_EXECUTED,
        tool_name=tool,
        actor_agent_id=deleted_by,
        reason=reason,
        target_id=f"{sub_id}@{version}",
    )
    return ok()


# --- workflow executions --------------------------------------------------
# Live handlers live in ``workflow_executions.py`` to keep this module
# under the project's 800-line ceiling. The four functions below re-bind
# them so the registry below stays self-documenting.

_workflow_executions_list = workflow_executions_list_impl
_workflow_executions_get = workflow_executions_get_impl
_workflow_executions_start = workflow_executions_start_impl
_workflow_executions_cancel = workflow_executions_cancel_impl


# --- workflow version history --------------------------------------------


async def _workflow_versions_list(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_workflow_versions_list"
    service = _version_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_VERSION_SERVICE)
    try:
        def_id = _require_non_blank(arguments, _ARG_DEF_ID)
        offset, limit = coerce_pagination(arguments)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        page, total = await service.list_versions(
            NotBlankStr(def_id),
            offset=offset,
            limit=limit,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    meta = PaginationMeta(total=total, offset=offset, limit=limit)
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=dump_many(page), pagination=meta)


async def _workflow_versions_get(
    *,
    app_state: Any,
    arguments: dict[str, Any],
    actor: AgentIdentity | None = None,  # noqa: ARG001
) -> str:
    tool = "synthorg_workflow_versions_get"
    service = _version_service(app_state)
    if service is None:
        return capability_gap(tool, _WHY_VERSION_SERVICE)
    try:
        def_id = _require_non_blank(arguments, _ARG_DEF_ID)
        revision = _require_int(arguments, _ARG_REVISION, positive=True)
    except ArgumentValidationError as exc:
        _log_invalid(tool, exc)
        return err(exc)
    try:
        snapshot = await service.get_version(NotBlankStr(def_id), revision)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        _log_failed(tool, exc)
        return err(exc)
    if snapshot is None:
        missing = WorkflowDefinitionNotFoundError(
            f"Workflow definition {def_id!r} revision {revision!r} not found",
        )
        _log_failed(tool, missing)
        return err(missing, domain_code="not_found")
    logger.info(MCP_HANDLER_INVOKE_SUCCESS, tool_name=tool)
    return ok(data=snapshot.model_dump(mode="json"))


WORKFLOW_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    copy.deepcopy(
        {
            "synthorg_workflows_list": _workflows_list,
            "synthorg_workflows_get": _workflows_get,
            "synthorg_workflows_create": _workflows_create,
            "synthorg_workflows_update": _workflows_update,
            "synthorg_workflows_delete": _workflows_delete,
            "synthorg_workflows_validate": _workflows_validate,
            "synthorg_subworkflows_list": _subworkflows_list,
            "synthorg_subworkflows_get": _subworkflows_get,
            "synthorg_subworkflows_create": _subworkflows_create,
            "synthorg_subworkflows_delete": _subworkflows_delete,
            "synthorg_workflow_executions_list": _workflow_executions_list,
            "synthorg_workflow_executions_get": _workflow_executions_get,
            "synthorg_workflow_executions_start": _workflow_executions_start,
            "synthorg_workflow_executions_cancel": _workflow_executions_cancel,
            "synthorg_workflow_versions_list": _workflow_versions_list,
            "synthorg_workflow_versions_get": _workflow_versions_get,
        },
    ),
)
