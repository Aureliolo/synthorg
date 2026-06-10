# module-kind: controller
"""Workflow definition CRUD controller."""

from datetime import UTC, datetime
from typing import Annotated

from litestar import Controller, Response, delete, get, patch, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from litestar.status_codes import HTTP_204_NO_CONTENT
from pydantic import ValidationError

from synthorg.api.auth import get_authenticated_user_id
from synthorg.api.controllers._workflow_builders import (
    apply_update,
    run_subworkflow_validation,
)
from synthorg.api.controllers.workflows._shared import _service
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.dto_workflow import (
    CreateWorkflowDefinitionRequest,
    UpdateWorkflowDefinitionRequest,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    WorkflowDefinitionValidationError,
    WorkflowTypeInvalidError,
)
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.service import WorkflowDefinitionNotFoundError
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    WORKFLOW_DEFINITION_CHANGE_REQUESTED,
    WORKFLOW_DEFINITION_CHANGED,
)
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_NOT_FOUND,
)

logger = get_logger(__name__)


WorkflowTypeFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by workflow type",
    ),
]


class WorkflowController(Controller):
    """CRUD for workflow definitions."""

    path = "/workflows"
    tags = ("workflows",)

    @get(guards=[require_read_access])
    async def list_workflows(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
        workflow_type: WorkflowTypeFilter = None,
    ) -> PaginatedResponse[WorkflowDefinition]:
        """List workflow definitions with optional filters.

        Returns:
            ``PaginatedResponse[WorkflowDefinition]`` instance.

        Raises:
            WorkflowTypeInvalidError: Raised on the corresponding failure path.
        """
        parsed_type: WorkflowType | None = None
        if workflow_type is not None:
            try:
                parsed_type = WorkflowType(workflow_type)
            except ValueError as exc:
                valid = ", ".join(e.value for e in WorkflowType)
                msg = f"Invalid workflow type: {workflow_type!r}. Valid: {valid}"
                raise WorkflowTypeInvalidError(msg) from exc

        # Over-fetch by one page so the cursor paginator can detect
        # has_more without a separate COUNT round-trip.
        defs = await _service(state).list_definitions(
            workflow_type=parsed_type,
            limit=limit + 1,
        )
        page, meta = paginate_cursor(
            defs,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse[WorkflowDefinition](
            data=page,
            pagination=meta,
        )

    @get("/{workflow_id:str}", guards=[require_read_access])
    async def get_workflow(
        self,
        state: State,
        workflow_id: PathId,
    ) -> ApiResponse[WorkflowDefinition]:
        """Get a workflow definition by ID.

        Returns the bare ``ApiResponse`` envelope (Litestar wraps it in
        a 200 response). A missing definition raises ``NotFoundError``
        (HTTP 404, ``WORKFLOW_DEFINITION_NOT_FOUND``) routed through the
        shared exception handlers rather than an inline 404 body.

        Args:
            state: Application state.
            workflow_id: Workflow identifier (1-128 chars, enforced at
                the path-parameter boundary by ``PathId``).

        Returns:
            ``ApiResponse[WorkflowDefinition]`` envelope wrapping the
            requested workflow definition.

        Raises:
            NotFoundError: The workflow definition does not exist.
            WorkflowDefinitionNotFoundError: Specific subclass raised
                when the definition is absent (subclass of
                ``NotFoundError`` for taxonomy discrimination).
        """
        definition = await _service(state).get_definition(workflow_id)
        if definition is None:
            logger.warning(
                WORKFLOW_DEF_NOT_FOUND,
                definition_id=workflow_id,
            )
            msg = f"workflow_definition {workflow_id!r} not found"
            raise WorkflowDefinitionNotFoundError(msg)
        return ApiResponse[WorkflowDefinition](data=definition)

    @post(
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("workflows.create", key="user"),
        ],
    )
    async def create_workflow(
        self,
        state: State,
        data: CreateWorkflowDefinitionRequest,
    ) -> Response[ApiResponse[WorkflowDefinition]]:
        """Create a new workflow definition.

        Returns:
            Result matching the declared return annotation.

        Raises:
            WorkflowDefinitionValidationError: Raised on the corresponding failure path.
        """
        creator = get_authenticated_user_id()
        now = datetime.now(UTC)
        try:
            nodes = tuple(WorkflowNode.model_validate(n) for n in data.nodes)
            edges = tuple(WorkflowEdge.model_validate(e) for e in data.edges)
            inputs = tuple(WorkflowIODeclaration.model_validate(i) for i in data.inputs)
            outputs = tuple(
                WorkflowIODeclaration.model_validate(o) for o in data.outputs
            )
            definition = WorkflowDefinition(
                name=data.name,
                description=data.description,
                workflow_type=data.workflow_type,
                version=data.version,
                inputs=inputs,
                outputs=outputs,
                is_subworkflow=data.is_subworkflow,
                nodes=nodes,
                edges=edges,
                created_by=creator,
                created_at=now,
                updated_at=now,
            )
        except (ValueError, ValidationError) as exc:
            msg = WorkflowDefinitionValidationError.default_message
            raise WorkflowDefinitionValidationError(msg) from exc

        subworkflow_errors = await run_subworkflow_validation(definition, state)
        if subworkflow_errors:
            messages = "; ".join(e.message for e in subworkflow_errors)
            msg = f"Subworkflow validation failed: {messages}"
            raise WorkflowDefinitionValidationError(msg)

        # Pre-persist intent log -- captures the operator's request
        # even if the write itself fails. ``WORKFLOW_DEFINITION_CHANGED``
        # below confirms actual success.
        logger.info(
            WORKFLOW_DEFINITION_CHANGE_REQUESTED,
            definition_id=str(definition.id),
            action="create",
            actor=creator,
            version_after=definition.version,
        )

        await _service(state).create_definition(definition, saved_by=creator)

        # Snapshot recording is handled inside ``WorkflowService`` via the
        # ``saved_by`` kwarg; no explicit ``snapshot_if_changed`` is needed.

        # Post-persist confirmation -- emitted only after the write
        # succeeds so the audit stream cannot record a "changed" hop
        # for a definition the database never accepted.
        logger.info(
            WORKFLOW_DEFINITION_CHANGED,
            definition_id=str(definition.id),
            action="create",
            actor=creator,
            version_after=definition.version,
        )

        return Response(
            content=ApiResponse[WorkflowDefinition](data=definition),
            status_code=201,
        )

    @patch(
        "/{workflow_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("workflows.update", key="user"),
        ],
    )
    async def update_workflow(
        self,
        state: State,
        workflow_id: PathId,
        data: UpdateWorkflowDefinitionRequest,
    ) -> Response[ApiResponse[WorkflowDefinition]]:
        """Update an existing workflow definition.

        Returns:
            Result matching the declared return annotation.

        Raises:
            WorkflowDefinitionValidationError: Raised on the corresponding failure path.
        """
        service = _service(state)
        existing = await service.fetch_for_update(
            workflow_id,
            data.expected_revision,
        )

        updated = apply_update(existing, data)

        subworkflow_errors = await run_subworkflow_validation(updated, state)
        if subworkflow_errors:
            messages = "; ".join(e.message for e in subworkflow_errors)
            msg = f"Subworkflow validation failed: {messages}"
            raise WorkflowDefinitionValidationError(msg)

        updater = get_authenticated_user_id()
        # Pre-persist intent log -- captures the operator's request
        # even if the update fails. ``WORKFLOW_DEFINITION_CHANGED``
        # below confirms actual success.
        logger.info(
            WORKFLOW_DEFINITION_CHANGE_REQUESTED,
            definition_id=updated.id,
            action="update",
            actor=updater,
            version_before=existing.version,
            version_after=updated.version,
        )
        await service.update_definition(updated, saved_by=updater)

        # Snapshot recording is handled inside ``WorkflowService`` via the
        # ``saved_by`` kwarg; no explicit ``snapshot_if_changed`` is needed.

        # Post-persist confirmation -- emitted only after the update
        # actually lands.
        logger.info(
            WORKFLOW_DEFINITION_CHANGED,
            definition_id=updated.id,
            action="update",
            actor=updater,
            version_before=existing.version,
            version_after=updated.version,
        )

        return Response(
            content=ApiResponse[WorkflowDefinition](data=updated),
        )

    @delete(
        "/{workflow_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("workflows.delete", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_workflow(
        self,
        state: State,
        workflow_id: PathId,
    ) -> None:
        """Delete a workflow definition and its version history.

        Raises:
            WorkflowDefinitionNotFoundError: Raised on the corresponding failure path.
        """
        actor = get_authenticated_user_id()
        # Pre-delete intent log -- captures the operator's request even
        # if the delete itself fails.
        logger.info(
            WORKFLOW_DEFINITION_CHANGE_REQUESTED,
            definition_id=workflow_id,
            action="delete",
            actor=actor,
        )
        deleted = await _service(state).delete_definition(workflow_id)
        if not deleted:
            logger.warning(
                WORKFLOW_DEF_NOT_FOUND,
                definition_id=workflow_id,
            )
            msg = f"workflow_definition {workflow_id!r} not found"
            raise WorkflowDefinitionNotFoundError(msg)
        # Post-delete confirmation -- emitted only on persistence success.
        logger.info(
            WORKFLOW_DEFINITION_CHANGED,
            definition_id=workflow_id,
            action="delete",
            actor=actor,
        )
