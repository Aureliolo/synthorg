# module-kind: controller
"""Workflow validation and YAML export controller."""

from uuid import uuid4

from litestar import Controller, Response, post
from litestar.datastructures import State
from pydantic import ValidationError

from synthorg.api.controllers._workflow_builders import run_subworkflow_validation
from synthorg.api.controllers.workflows._shared import _service
from synthorg.api.dto import ApiResponse
from synthorg.api.dto_workflow import CreateWorkflowDefinitionRequest
from synthorg.api.guards import require_read_access
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.engine.errors import (
    WorkflowDefinitionValidationError,
    WorkflowYamlExportError,
)
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.service import WorkflowDefinitionNotFoundError
from synthorg.engine.workflow.validation import WorkflowValidationResult
from synthorg.engine.workflow.validation import (
    validate_workflow as run_workflow_validation,
)
from synthorg.engine.workflow.yaml_export import export_workflow_yaml
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_REQUEST_ERROR
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_NOT_FOUND,
)

logger = get_logger(__name__)


class WorkflowValidationController(Controller):
    """Validation and YAML export for workflow definitions."""

    path = "/workflows"
    tags = ("workflows",)

    @post(
        "/validate-draft",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy(
                "workflows.validate_draft",
                key="user_or_ip",
            ),
        ],
        status_code=200,
    )
    async def validate_draft(
        self,
        state: State,
        data: CreateWorkflowDefinitionRequest,
    ) -> Response[ApiResponse[WorkflowValidationResult]]:
        """Validate a draft workflow without persisting.

        Returns:
            Result matching the declared return annotation.

        Raises:
            WorkflowDefinitionValidationError: Raised on the corresponding failure path.
        """
        try:
            nodes = tuple(WorkflowNode.model_validate(n) for n in data.nodes)
            edges = tuple(WorkflowEdge.model_validate(e) for e in data.edges)
            inputs = tuple(WorkflowIODeclaration.model_validate(i) for i in data.inputs)
            outputs = tuple(
                WorkflowIODeclaration.model_validate(o) for o in data.outputs
            )
            definition = WorkflowDefinition(
                id=uuid4(),
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
            logger.warning(
                API_REQUEST_ERROR,
                endpoint="workflows.validate_draft",
                workflow_name=data.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
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

    @post(
        "/{workflow_id:str}/validate",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("workflows.validate", key="user"),
        ],
        status_code=200,
    )
    async def validate_workflow(
        self,
        state: State,
        workflow_id: PathId,
    ) -> ApiResponse[WorkflowValidationResult]:
        """Validate a workflow definition for execution readiness.

        Returns the bare ``ApiResponse`` envelope (Litestar wraps it in
        a 200 response). A missing definition raises ``NotFoundError``
        (HTTP 404, ``WORKFLOW_DEFINITION_NOT_FOUND``) via the shared
        exception handlers instead of an inline 404 body.

        Returns:
            ``ApiResponse[WorkflowValidationResult]`` envelope wrapping
            the validation outcome.

        Raises:
            NotFoundError: The workflow definition does not exist.
            WorkflowDefinitionNotFoundError: Specific subclass raised
                when the definition is absent.
        """
        definition = await _service(state).get_definition(workflow_id)
        if definition is None:
            logger.warning(
                WORKFLOW_DEF_NOT_FOUND,
                definition_id=workflow_id,
            )
            msg = f"workflow_definition {workflow_id!r} not found"
            raise WorkflowDefinitionNotFoundError(msg)

        result = run_workflow_validation(definition)
        return ApiResponse[WorkflowValidationResult](data=result)

    @post(
        "/{workflow_id:str}/export",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("workflows.export", key="user"),
        ],
        status_code=200,
    )
    async def export_workflow(
        self,
        state: State,
        workflow_id: PathId,
    ) -> Response[str]:
        """Export a workflow definition as YAML.

        Returns only ``Response[str]`` on success; a missing definition
        raises ``NotFoundError`` (HTTP 404,
        ``WORKFLOW_DEFINITION_NOT_FOUND``) through the shared exception
        handlers rather than returning an inline 404 response.

        Returns:
            ``Response[str]`` wrapping the exported YAML payload.

        Raises:
            NotFoundError: The workflow definition does not exist.
            WorkflowDefinitionNotFoundError: Specific subclass raised
                when the definition is absent.
            WorkflowYamlExportError: The YAML serialiser failed to
                emit a valid document.
        """
        definition = await _service(state).get_definition(workflow_id)
        if definition is None:
            logger.warning(
                WORKFLOW_DEF_NOT_FOUND,
                definition_id=workflow_id,
            )
            msg = f"workflow_definition {workflow_id!r} not found"
            raise WorkflowDefinitionNotFoundError(msg)

        try:
            yaml_str = export_workflow_yaml(definition)
        except ValueError as exc:
            logger.warning(
                API_REQUEST_ERROR,
                endpoint="workflows.export",
                definition_id=workflow_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Export failed: {safe_error_description(exc)}"
            raise WorkflowYamlExportError(msg) from exc

        return Response(
            content=yaml_str,
            media_type="text/yaml",
        )
