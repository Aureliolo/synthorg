"""Workflow execution controller -- activate, list, get, cancel."""

from typing import Any, Final

from litestar import Controller, Request, Response, get, post
from litestar.datastructures import State  # noqa: TC002

from synthorg._core.features import require_service
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.dto_workflow import (
    ActivateWorkflowRequest,  # noqa: TC001 -- Litestar resolves request-body annotations at runtime
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.responses import require_resource_or_404
from synthorg.core.domain_errors import NotFoundError, VersionConflictError
from synthorg.core.persistence_errors import (
    PersistenceVersionConflictError,
    RecordNotFoundError,
)
from synthorg.engine.errors import (
    WorkflowExecutionNotFoundError,
)
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.workflow.execution_models import WorkflowExecution
from synthorg.engine.workflow.execution_service import WorkflowExecutionService
from synthorg.observability import get_logger
from synthorg.observability.events.workflow_execution import (
    WORKFLOW_EXEC_CANCELLED,
    WORKFLOW_EXEC_NOT_FOUND,
    WORKFLOW_EXECUTION_USERNAME_FALLBACK,
)
from synthorg.persistence.state import persistence_of
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_DEFAULT_PAGE_SIZE: Final[int] = 50


def _extract_username(request: Request[Any, Any, Any]) -> str:
    """Extract username from the request, falling back to ``"api"``.

    Treats ``None`` and empty-string usernames as missing so the
    fallback warning fires for those cases too -- ``str(None)`` would
    otherwise persist the literal string ``"None"`` as the actor on
    workflow audit entries.

    Returns:
        Resulting string.
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
    code default, via :class:`SettingsService` / :class:`ConfigResolver`)
    on every request. The ``config_resolver`` slot is mandatory: an
    unwired resolver raises a 503 instead of silently falling back to
    the Pydantic default, so a wiring failure surfaces immediately
    rather than running executions with code defaults.

    Returns:
        ``WorkflowExecutionService`` instance.

    Raises:
        ServiceUnavailableError: When ``config_resolver`` is not wired
            (surfaced through ``config_resolver_of``).
    """
    app_state = state.app_state
    # Resolver is mandatory: silently falling back to ``EngineBridgeConfig()``
    # would hide a wiring failure and run executions with the code default
    # instead of the resolved DB / env value (Cat-1 precedence).
    engine_bridge = await config_resolver_of(app_state).get_engine_bridge_config()
    return WorkflowExecutionService(
        definition_repo=persistence_of(app_state).workflow_definitions,
        execution_repo=persistence_of(app_state).workflow_executions,
        task_engine=require_service(
            app_state.slice(EngineStateSlice).task_engine, "Task Engine"
        ),
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

        Returns:
            ``Response[ApiResponse[WorkflowExecution]]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
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
        limit: CursorLimit = _DEFAULT_PAGE_SIZE,
    ) -> Response[PaginatedResponse[WorkflowExecution] | ApiResponse[None]]:
        """List executions for a workflow definition with cursor pagination.

        Returns:
            Result matching the declared return annotation.
        """
        service = await _build_service(state)
        # Over-fetch by one page so the cursor paginator can detect
        # has_more without a separate COUNT round-trip.
        executions = await service.list_executions(workflow_id, limit=limit + 1)
        page, meta = paginate_cursor(
            tuple(executions),
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
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
        """Get a specific workflow execution.

        Returns:
            ``Response[ApiResponse[WorkflowExecution]]`` instance.

        Raises:
            WorkflowExecutionNotFoundError: If no execution exists for
                ``execution_id``.
        """
        service = await _build_service(state)
        execution = await service.get_execution(execution_id)
        execution = require_resource_or_404(
            execution,
            resource_type="Workflow execution",
            identifier=execution_id,
            log_event=WORKFLOW_EXEC_NOT_FOUND,
            operation="read",
            extra_log_kwargs={"execution_id": execution_id},
            error_class=WorkflowExecutionNotFoundError,
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
        race (``VERSION_CONFLICT``, re-read and retry).
        ``WorkflowExecutionAlreadyTerminalError`` propagates from the
        engine; only the persistence-layer race is re-mapped here.
        ``PersistenceError`` (500) propagates unchanged.

        Returns:
            ``Response[ApiResponse[WorkflowExecution]]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
            VersionConflictError: Raised on the corresponding failure path.
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
        except PersistenceVersionConflictError as exc:
            # Drop the persistence-layer detail (row IDs, version
            # numbers) on the public envelope; the engine emits
            # ``WORKFLOW_EXEC_CANCEL_CONFLICT`` with the scrubbed
            # exception attributes for audit. ``from exc`` keeps the
            # original chained for traceback inspection.
            raise VersionConflictError from exc

        logger.info(
            WORKFLOW_EXEC_CANCELLED,
            execution_id=execution_id,
            cancelled_by=cancelled_by,
        )
        return Response(
            content=ApiResponse[WorkflowExecution](data=execution),
        )
