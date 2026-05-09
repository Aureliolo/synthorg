"""Workflow definition controller -- CRUD, validation, and YAML export."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from litestar import Controller, Request, Response, delete, get, patch, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from litestar.status_codes import HTTP_204_NO_CONTENT
from pydantic import ValidationError

from synthorg.api.controllers._workflow_builders import (
    apply_update,
    build_definition_from_blueprint,
    load_blueprint_or_raise,
    run_subworkflow_validation,
    wf_versioning,
)
from synthorg.api.controllers._workflow_helpers import get_auth_user_id
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.dto_workflow import (
    BlueprintInfoResponse,
    CreateFromBlueprintRequest,
    CreateWorkflowDefinitionRequest,
    UpdateWorkflowDefinitionRequest,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.domain_errors import NotFoundError
from synthorg.core.enums import WorkflowType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    WorkflowDefinitionValidationError,
    WorkflowTypeInvalidError,
    WorkflowYamlExportError,
)
from synthorg.engine.workflow.blueprint_loader import list_blueprints
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.service import WorkflowService
from synthorg.engine.workflow.validation import WorkflowValidationResult
from synthorg.engine.workflow.validation import (
    validate_workflow as run_workflow_validation,
)
from synthorg.engine.workflow.yaml_export import export_workflow_yaml
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    WORKFLOW_DEFINITION_CHANGE_REQUESTED,
    WORKFLOW_DEFINITION_CHANGED,
)
from synthorg.observability.events.blueprint import (
    BLUEPRINT_INSTANTIATE_START,
    BLUEPRINT_INSTANTIATE_SUCCESS,
)
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_NOT_FOUND,
)
from synthorg.observability.metrics_hub import record_blueprint_instantiation

logger = get_logger(__name__)


def _service(state: State) -> WorkflowService:
    """Build the per-request :class:`WorkflowService`.

    Wires in the :class:`VersioningService` for workflow definitions so
    create/update paths persist a best-effort version snapshot in the
    same service call -- controllers no longer orchestrate the two
    writes by hand.
    """
    return WorkflowService(
        definition_repo=state.app_state.persistence.workflow_definitions,
        version_repo=state.app_state.persistence.workflow_versions,
        versioning_service=wf_versioning(state),
    )


WorkflowTypeFilter = Annotated[
    NotBlankStr | None,
    Parameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by workflow type",
    ),
]


class WorkflowController(Controller):
    """CRUD, validation, and export for workflow definitions."""

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
        """List workflow definitions with optional filters."""
        parsed_type: WorkflowType | None = None
        if workflow_type is not None:
            try:
                parsed_type = WorkflowType(workflow_type)
            except ValueError as exc:
                valid = ", ".join(e.value for e in WorkflowType)
                msg = f"Invalid workflow type: {workflow_type!r}. Valid: {valid}"
                raise WorkflowTypeInvalidError(msg) from exc

        defs = await _service(state).list_definitions(workflow_type=parsed_type)
        page, meta = paginate_cursor(
            defs,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return PaginatedResponse[WorkflowDefinition](
            data=page,
            pagination=meta,
        )

    @get("/blueprints", guards=[require_read_access])
    async def list_workflow_blueprints(
        self,
    ) -> Response[ApiResponse[tuple[BlueprintInfoResponse, ...]]]:
        """List available workflow blueprints."""
        infos = await asyncio.to_thread(list_blueprints)
        responses = tuple(
            BlueprintInfoResponse(
                name=i.name,
                display_name=i.display_name,
                description=i.description,
                source=i.source,
                tags=i.tags,
                workflow_type=WorkflowType(i.workflow_type),
                node_count=i.node_count,
                edge_count=i.edge_count,
            )
            for i in infos
        )
        return Response(
            content=ApiResponse[tuple[BlueprintInfoResponse, ...]](
                data=responses,
            ),
        )

    @post(
        "/from-blueprint",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy(
                "workflows.create_from_blueprint",
                key="user",
            ),
        ],
    )
    async def create_from_blueprint(
        self,
        request: Request[Any, Any, Any],
        state: State,
        data: CreateFromBlueprintRequest,
    ) -> Response[ApiResponse[WorkflowDefinition]]:
        """Create a new workflow definition from a blueprint."""
        creator = get_auth_user_id(request)
        logger.info(
            BLUEPRINT_INSTANTIATE_START,
            blueprint_name=data.blueprint_name,
        )

        bp = await load_blueprint_or_raise(data.blueprint_name)

        now = datetime.now(UTC)
        definition = build_definition_from_blueprint(
            bp,
            data,
            creator,
            now,
        )

        await _service(state).create_definition(definition, saved_by=creator)

        # Snapshot orchestration moved into ``WorkflowService.create_definition``
        # (via the ``saved_by`` kwarg), so no explicit ``snapshot_if_changed``
        # call is needed here.
        logger.info(
            BLUEPRINT_INSTANTIATE_SUCCESS,
            definition_id=definition.id,
            blueprint_name=data.blueprint_name,
        )
        record_blueprint_instantiation(
            outcome="success",
            blueprint_name=data.blueprint_name,
        )
        return Response(
            content=ApiResponse[WorkflowDefinition](data=definition),
            status_code=201,
        )

    @get("/{workflow_id:str}", guards=[require_read_access])
    async def get_workflow(
        self,
        state: State,
        workflow_id: PathId,
    ) -> Response[ApiResponse[WorkflowDefinition]]:
        """Get a workflow definition by ID."""
        definition = await _service(state).get_definition(workflow_id)
        if definition is None:
            logger.warning(
                WORKFLOW_DEF_NOT_FOUND,
                definition_id=workflow_id,
            )
            return Response(
                content=ApiResponse[WorkflowDefinition](
                    error="Workflow definition not found",
                ),
                status_code=404,
            )
        return Response(
            content=ApiResponse[WorkflowDefinition](data=definition),
        )

    @post(
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("workflows.create", key="user"),
        ],
    )
    async def create_workflow(
        self,
        request: Request[Any, Any, Any],
        state: State,
        data: CreateWorkflowDefinitionRequest,
    ) -> Response[ApiResponse[WorkflowDefinition]]:
        """Create a new workflow definition."""
        creator = get_auth_user_id(request)
        now = datetime.now(UTC)
        try:
            nodes = tuple(WorkflowNode.model_validate(n) for n in data.nodes)
            edges = tuple(WorkflowEdge.model_validate(e) for e in data.edges)
            inputs = tuple(WorkflowIODeclaration.model_validate(i) for i in data.inputs)
            outputs = tuple(
                WorkflowIODeclaration.model_validate(o) for o in data.outputs
            )
            definition = WorkflowDefinition(
                id=f"wfdef-{uuid.uuid4().hex[:12]}",
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
            definition_id=definition.id,
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
            definition_id=definition.id,
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
        request: Request[Any, Any, Any],
        state: State,
        workflow_id: PathId,
        data: UpdateWorkflowDefinitionRequest,
    ) -> Response[ApiResponse[WorkflowDefinition]]:
        """Update an existing workflow definition."""
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

        updater = get_auth_user_id(request)
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
        request: Request[Any, Any, Any],
        state: State,
        workflow_id: PathId,
    ) -> None:
        """Delete a workflow definition and its version history."""
        actor = get_auth_user_id(request)
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
            msg = "Workflow definition not found"
            raise NotFoundError(msg)
        # Post-delete confirmation -- emitted only on persistence success.
        logger.info(
            WORKFLOW_DEFINITION_CHANGED,
            definition_id=workflow_id,
            action="delete",
            actor=actor,
        )

    @post("/validate-draft", guards=[require_read_access], status_code=200)
    async def validate_draft(
        self,
        state: State,
        data: CreateWorkflowDefinitionRequest,
    ) -> Response[ApiResponse[WorkflowValidationResult]]:
        """Validate a draft workflow without persisting."""
        try:
            nodes = tuple(WorkflowNode.model_validate(n) for n in data.nodes)
            edges = tuple(WorkflowEdge.model_validate(e) for e in data.edges)
            inputs = tuple(WorkflowIODeclaration.model_validate(i) for i in data.inputs)
            outputs = tuple(
                WorkflowIODeclaration.model_validate(o) for o in data.outputs
            )
            definition = WorkflowDefinition(
                id="draft",
                name=data.name,
                description=data.description,
                workflow_type=data.workflow_type,
                version=data.version,
                inputs=inputs,
                outputs=outputs,
                is_subworkflow=data.is_subworkflow,
                nodes=nodes,
                edges=edges,
                created_by="draft",
            )
        except (ValueError, ValidationError) as exc:
            msg = WorkflowDefinitionValidationError.default_message
            raise WorkflowDefinitionValidationError(msg) from exc

        result = run_workflow_validation(definition)

        subworkflow_errors = await run_subworkflow_validation(
            definition,
            state,
        )
        if subworkflow_errors:
            result = WorkflowValidationResult(
                errors=result.errors + subworkflow_errors,
            )

        return Response(
            content=ApiResponse[WorkflowValidationResult](
                data=result,
            ),
        )

    @post("/{workflow_id:str}/validate", guards=[require_read_access], status_code=200)
    async def validate_workflow(
        self,
        state: State,
        workflow_id: PathId,
    ) -> Response[ApiResponse[WorkflowValidationResult]]:
        """Validate a workflow definition for execution readiness."""
        definition = await _service(state).get_definition(workflow_id)
        if definition is None:
            logger.warning(
                WORKFLOW_DEF_NOT_FOUND,
                definition_id=workflow_id,
            )
            return Response(
                content=ApiResponse[WorkflowValidationResult](
                    error="Workflow definition not found",
                ),
                status_code=404,
            )

        result = run_workflow_validation(definition)
        return Response(
            content=ApiResponse[WorkflowValidationResult](
                data=result,
            ),
        )

    @post("/{workflow_id:str}/export", guards=[require_read_access], status_code=200)
    async def export_workflow(
        self,
        state: State,
        workflow_id: PathId,
    ) -> Response[str] | Response[ApiResponse[None]]:
        """Export a workflow definition as YAML."""
        definition = await _service(state).get_definition(workflow_id)
        if definition is None:
            logger.warning(
                WORKFLOW_DEF_NOT_FOUND,
                definition_id=workflow_id,
            )
            return Response(
                content=ApiResponse[None](
                    error="Workflow definition not found",
                ),
                status_code=404,
            )

        try:
            yaml_str = export_workflow_yaml(definition)
        except ValueError as exc:
            msg = f"Export failed: {safe_error_description(exc)}"
            raise WorkflowYamlExportError(msg) from exc

        return Response(
            content=yaml_str,
            media_type="text/yaml",
        )
