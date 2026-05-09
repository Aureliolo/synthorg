"""Workflow execution controller -- activate, list, get, cancel."""

from typing import Any

from litestar import Controller, Request, Response, get, post
from litestar.datastructures import State  # noqa: TC002

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_workflow import (
    ActivateWorkflowRequest,  # noqa: TC001 -- Litestar resolves request-body annotations at runtime
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    paginate_cursor,
)
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.core.domain_errors import NotFoundError, VersionConflictError
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.core.persistence_errors import (
    PersistenceVersionConflictError,
    RecordNotFoundError,
)
from synthorg.engine.errors import (
    WorkflowExecutionAlreadyTerminalError,
    WorkflowExecutionError,
    WorkflowExecutionNotFoundError,
)
from synthorg.engine.workflow.execution_models import WorkflowExecution
from synthorg.engine.workflow.execution_service import WorkflowExecutionService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workflow_execution import (
    WORKFLOW_EXEC_CANCELLED,
    WORKFLOW_EXEC_NOT_FOUND,
    WORKFLOW_EXECUTION_USERNAME_FALLBACK,
)

logger = get_logger(__name__)


def _extract_username(request: Request[Any, Any, Any]) -> str:
    """Extract username from the request, falling back to ``"api"``.

    Treats ``None`` and empty-string usernames as missing so the
    fallback warning fires for those cases too -- ``str(None)`` would
    otherwise persist the literal string ``"None"`` as the actor on
    workflow audit entries.
    """
    user = getattr(request, "user", None)
    if user is not None:
        username = getattr(user, "username", None)
        if isinstance(username, str):
            stripped = username.strip()
            if stripped:
                return stripped
        elif username:
            return str(username)
    logger.warning(
        WORKFLOW_EXECUTION_USERNAME_FALLBACK,
        note="request has no usable username, using 'api'",
        path=str(request.url),
    )
    return "api"


async def _build_service(state: State) -> WorkflowExecutionService:
    """Construct a WorkflowExecutionService from app state.

    Resolves ``engine.max_subworkflow_depth`` through the engine bridge
    config so the service inherits the operator's settings (DB > env >
    YAML > code default) on every request. When ``config_resolver`` is
    unavailable (test fixtures wiring the controller without the full
    settings stack), falls back to the bridge config's Pydantic
    default so the service still receives a valid depth limit.
    """
    from synthorg.settings.bridge_configs import (  # noqa: PLC0415
        EngineBridgeConfig,
    )

    app_state = state.app_state
    if app_state.has_config_resolver:
        engine_bridge = await app_state.config_resolver.get_engine_bridge_config()
    else:
        engine_bridge = EngineBridgeConfig()
    return WorkflowExecutionService(
        definition_repo=app_state.persistence.workflow_definitions,
        execution_repo=app_state.persistence.workflow_executions,
        task_engine=app_state.task_engine,
        max_subworkflow_depth=engine_bridge.max_subworkflow_depth,
    )


class WorkflowExecutionController(Controller):
    """Activate, list, get, and cancel workflow executions."""

    path = "/workflow-executions"
    tags = ("workflow-executions",)

    @post(
        "/activate/{workflow_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("workflows.activate", key="user"),
        ],
        status_code=201,
    )
    async def activate_workflow(
        self,
        request: Request[Any, Any, Any],
        state: State,
        workflow_id: PathId,
        data: ActivateWorkflowRequest,
    ) -> Response[ApiResponse[WorkflowExecution]]:
        """Activate a workflow definition, creating task instances.

        ``WorkflowDefinitionInvalidError`` (422), ``WorkflowConditionEvalError``
        (422) and ``PersistenceError`` (500) propagate to the centralised
        RFC 9457 dispatch in ``api/exception_handlers.py``.
        """
        activated_by = _extract_username(request)
        service = await _build_service(state)
        try:
            execution = await service.activate(
                workflow_id,
                project=data.project,
                activated_by=activated_by,
                context=data.context,
            )
        except WorkflowExecutionNotFoundError:
            logger.warning(
                WORKFLOW_EXEC_NOT_FOUND,
                workflow_id=workflow_id,
            )
            msg = f"Workflow definition {workflow_id!r} not found"
            raise NotFoundError(msg) from None

        return Response(
            content=ApiResponse[WorkflowExecution](data=execution),
            status_code=201,
        )

    @get(
        "/by-definition/{workflow_id:str}",
        guards=[require_read_access],
    )
    async def list_executions(
        self,
        state: State,
        workflow_id: PathId,
        cursor: CursorParam = None,
        limit: CursorLimit = 50,  # lint-allow: magic-numbers -- pagination default
    ) -> Response[PaginatedResponse[WorkflowExecution] | ApiResponse[None]]:
        """List executions for a workflow definition with cursor pagination."""
        service = await _build_service(state)
        executions = await service.list_executions(workflow_id)
        page, meta = paginate_cursor(
            tuple(executions),
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return Response(
            content=PaginatedResponse[WorkflowExecution](
                data=page,
                pagination=meta,
            ),
        )

    @get(
        "/{execution_id:str}",
        guards=[require_read_access],
    )
    async def get_execution(
        self,
        state: State,
        execution_id: PathId,
    ) -> Response[ApiResponse[WorkflowExecution]]:
        """Get a specific workflow execution."""
        service = await _build_service(state)
        execution = await service.get_execution(execution_id)
        execution = require_resource_or_404(
            execution,
            resource_type="Workflow execution",
            identifier=execution_id,
            log_event=WORKFLOW_EXEC_NOT_FOUND,
            operation="read",
            extra_log_kwargs={"execution_id": execution_id},
            code=ErrorCode.WORKFLOW_EXECUTION_NOT_FOUND,
        )
        return Response(
            content=ApiResponse[WorkflowExecution](data=execution),
        )

    @post(
        "/{execution_id:str}/cancel",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("workflows.cancel", key="user"),
        ],
    )
    async def cancel_execution(
        self,
        request: Request[Any, Any, Any],
        state: State,
        execution_id: PathId,
    ) -> Response[ApiResponse[WorkflowExecution]]:
        """Cancel a workflow execution.

        Rejection paths translate to 409 ``CONFLICT`` with a discriminating
        ``error_code`` so clients can distinguish "execution finished
        before you cancelled" (``WORKFLOW_EXECUTION_ALREADY_TERMINAL``,
        no retry will succeed) from a row-level optimistic-concurrency
        race (``VERSION_CONFLICT``, re-read and retry). ``PersistenceError``
        (500) propagates unchanged. The engine layer emits
        ``WORKFLOW_EXEC_CANCEL_CONFLICT`` before raising so audit-stream
        alerting on failed cancels is preserved.
        """
        cancelled_by = _extract_username(request)
        service = await _build_service(state)
        try:
            execution = await service.cancel_execution(
                execution_id,
                cancelled_by=cancelled_by,
            )
        except WorkflowExecutionNotFoundError, RecordNotFoundError:
            logger.warning(
                WORKFLOW_EXEC_NOT_FOUND,
                execution_id=execution_id,
            )
            msg = f"Workflow execution {execution_id!r} not found"
            raise NotFoundError(msg) from None
        except WorkflowExecutionError as exc:
            scrubbed = safe_error_description(exc)
            raise WorkflowExecutionAlreadyTerminalError(scrubbed) from exc
        except PersistenceVersionConflictError as exc:
            scrubbed = safe_error_description(exc)
            raise VersionConflictError(scrubbed) from exc

        logger.info(
            WORKFLOW_EXEC_CANCELLED,
            execution_id=execution_id,
            cancelled_by=cancelled_by,
        )
        return Response(
            content=ApiResponse[WorkflowExecution](data=execution),
        )
