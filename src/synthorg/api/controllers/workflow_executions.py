"""Workflow execution controller -- activate, list, get, cancel."""

from typing import Any

from litestar import Controller, Request, Response, get, post
from litestar.datastructures import State  # noqa: TC002

from synthorg.api.dto import (
    ActivateWorkflowRequest,
    ApiResponse,
    PaginatedResponse,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    paginate_cursor,
)
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.persistence_errors import PersistenceError, VersionConflictError
from synthorg.engine.errors import (
    WorkflowConditionEvalError,
    WorkflowDefinitionInvalidError,
    WorkflowExecutionError,
    WorkflowExecutionNotFoundError,
)
from synthorg.engine.workflow.execution_models import WorkflowExecution
from synthorg.engine.workflow.execution_service import WorkflowExecutionService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workflow_execution import (
    WORKFLOW_EXEC_CANCELLED,
    WORKFLOW_EXEC_CONDITION_EVAL_FAILED,
    WORKFLOW_EXEC_INVALID_DEFINITION,
    WORKFLOW_EXEC_NOT_FOUND,
    WORKFLOW_EXEC_PERSISTENCE_FAILED,
    WORKFLOW_EXECUTION_USERNAME_FALLBACK,
)

logger = get_logger(__name__)


def _extract_username(request: Request[Any, Any, Any]) -> str:
    """Extract username from the request, falling back to ``"api"``."""
    user = getattr(request, "user", None)
    if user and hasattr(user, "username"):
        return str(user.username)
    logger.warning(
        WORKFLOW_EXECUTION_USERNAME_FALLBACK,
        note="request has no user or username attribute, using 'api'",
        path=str(request.url),
    )
    return "api"


def _build_service(state: State) -> WorkflowExecutionService:
    """Construct a WorkflowExecutionService from app state."""
    app_state = state.app_state
    return WorkflowExecutionService(
        definition_repo=app_state.persistence.workflow_definitions,
        execution_repo=app_state.persistence.workflow_executions,
        task_engine=app_state.task_engine,
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
        """Activate a workflow definition, creating task instances."""
        activated_by = _extract_username(request)
        service = _build_service(state)
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
        except WorkflowDefinitionInvalidError as exc:
            logger.warning(
                WORKFLOW_EXEC_INVALID_DEFINITION,
                workflow_id=workflow_id,
                error=str(exc),
            )
            return Response(
                content=ApiResponse[WorkflowExecution](error=str(exc)),
                status_code=422,
            )
        except WorkflowConditionEvalError as exc:
            logger.warning(
                WORKFLOW_EXEC_CONDITION_EVAL_FAILED,
                workflow_id=workflow_id,
                error=str(exc),
            )
            return Response(
                content=ApiResponse[WorkflowExecution](error=str(exc)),
                status_code=422,
            )
        except PersistenceError as exc:
            logger.warning(
                WORKFLOW_EXEC_PERSISTENCE_FAILED,
                workflow_id=workflow_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="persistence failure during activation",
            )
            return Response(
                content=ApiResponse[WorkflowExecution](
                    error="Workflow activation failed due to a storage error.",
                ),
                status_code=500,
            )

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
        limit: CursorLimit = 50,
    ) -> Response[PaginatedResponse[WorkflowExecution] | ApiResponse[None]]:
        """List executions for a workflow definition with cursor pagination."""
        service = _build_service(state)
        try:
            executions = await service.list_executions(workflow_id)
        except PersistenceError as exc:
            logger.warning(
                WORKFLOW_EXEC_PERSISTENCE_FAILED,
                workflow_id=workflow_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="persistence failure during list",
            )
            return Response(
                content=ApiResponse[None](
                    error="Failed to list workflow executions.",
                ),
                status_code=500,
            )
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
        service = _build_service(state)
        try:
            execution = await service.get_execution(execution_id)
        except PersistenceError as exc:
            logger.warning(
                WORKFLOW_EXEC_PERSISTENCE_FAILED,
                execution_id=execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="persistence failure during get",
            )
            return Response(
                content=ApiResponse[WorkflowExecution](
                    error="Failed to retrieve workflow execution.",
                ),
                status_code=500,
            )
        if execution is None:
            logger.warning(
                WORKFLOW_EXEC_NOT_FOUND,
                execution_id=execution_id,
            )
            msg = f"Workflow execution {execution_id!r} not found"
            raise NotFoundError(msg)

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
        """Cancel a workflow execution."""
        cancelled_by = _extract_username(request)
        service = _build_service(state)
        try:
            execution = await service.cancel_execution(
                execution_id,
                cancelled_by=cancelled_by,
            )
        except WorkflowExecutionNotFoundError:
            logger.warning(
                WORKFLOW_EXEC_NOT_FOUND,
                execution_id=execution_id,
            )
            msg = f"Workflow execution {execution_id!r} not found"
            raise NotFoundError(msg) from None
        except (WorkflowExecutionError, VersionConflictError) as exc:
            logger.warning(
                WORKFLOW_EXEC_CANCELLED,
                execution_id=execution_id,
                error=str(exc),
                note="cancel conflict",
            )
            return Response(
                content=ApiResponse[WorkflowExecution](error=str(exc)),
                status_code=409,
            )
        except PersistenceError as exc:
            logger.warning(
                WORKFLOW_EXEC_PERSISTENCE_FAILED,
                execution_id=execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="persistence failure during cancel",
            )
            return Response(
                content=ApiResponse[WorkflowExecution](
                    error="Failed to cancel workflow execution.",
                ),
                status_code=500,
            )

        return Response(
            content=ApiResponse[WorkflowExecution](data=execution),
        )
