"""Workflow controller builders -- blueprint loaders, update appliers.

Extracted from ``workflows.py`` to keep that controller focused on
the Litestar route handlers.
"""

import asyncio
import copy
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from litestar.datastructures import State
from pydantic import ValidationError

from synthorg.engine.errors import WorkflowDefinitionValidationError
from synthorg.engine.workflow.blueprint_errors import (
    BlueprintNotFoundError,
    BlueprintValidationError,
)
from synthorg.engine.workflow.blueprint_loader import load_blueprint
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.subworkflow_registry import SubworkflowRegistry
from synthorg.engine.workflow.validation import (
    validate_subworkflow_graph,
    validate_subworkflow_io,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.blueprint import BLUEPRINT_INSTANTIATE_FAILED
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_INVALID_REQUEST,
)
from synthorg.observability.metrics_hub import record_blueprint_instantiation
from synthorg.persistence.state import persistence_of
from synthorg.versioning import VersioningService

if TYPE_CHECKING:
    from synthorg.api.dto_workflow import (
        CreateFromBlueprintRequest,
        UpdateWorkflowDefinitionRequest,
    )
    from synthorg.engine.workflow.blueprint_models import BlueprintData
    from synthorg.engine.workflow.validation import WorkflowValidationError

logger = get_logger(__name__)


def wf_versioning(state: State) -> VersioningService[WorkflowDefinition]:
    """Build a VersioningService for workflow definitions.

    Returns:
        ``VersioningService[WorkflowDefinition]`` instance.
    """
    return VersioningService(persistence_of(state.app_state).workflow_versions)


async def run_subworkflow_validation(
    definition: WorkflowDefinition,
    state: State,
) -> tuple[WorkflowValidationError, ...]:
    """Run save-time subworkflow I/O + cycle validation.

    Returns:
        Tuple of the declared element types.
    """
    registry = SubworkflowRegistry(persistence_of(state.app_state).subworkflows)
    io_result = await validate_subworkflow_io(definition, registry)
    graph_result = await validate_subworkflow_graph(definition, registry)
    return tuple(io_result.errors) + tuple(graph_result.errors)


def _scalar_updates(
    data: UpdateWorkflowDefinitionRequest,
) -> dict[str, object]:
    """Extract simple scalar field updates from the request.

    Returns:
        Mapping with the declared key/value types.
    """
    updates: dict[str, object] = {"updated_at": datetime.now(UTC)}
    for field in ("name", "description", "workflow_type", "version", "is_subworkflow"):
        value = getattr(data, field)
        if value is not None:
            updates[field] = value
    return updates


def _validate_collection(
    items: object,
    model_cls: type,
    *,
    field_name: str,
) -> tuple[object, ...]:
    """Validate an iterable of dict items against ``model_cls``.

    Raises:
        WorkflowDefinitionValidationError: 422 with a field-scoped
            message so API clients see which collection failed without
            needing to consult server logs. Pydantic detail is scrubbed
            to avoid leaking internal payload shapes.

    Returns:
        Tuple of the declared element types.
    """
    try:
        return tuple(model_cls.model_validate(i) for i in items)  # type: ignore[attr-defined]
    except (ValueError, ValidationError) as exc:
        logger.warning(
            WORKFLOW_DEF_INVALID_REQUEST,
            field=field_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Invalid {field_name} field in request."
        raise WorkflowDefinitionValidationError(msg) from exc


def build_update_fields(
    data: UpdateWorkflowDefinitionRequest,
) -> dict[str, object]:
    """Build the update dict from the request, raising on invalid fields.

    Returns:
        Mapping with the declared key/value types.
    """
    updates = _scalar_updates(data)

    collection_specs: tuple[tuple[str, object, type], ...] = (
        ("inputs", data.inputs, WorkflowIODeclaration),
        ("outputs", data.outputs, WorkflowIODeclaration),
        ("nodes", data.nodes, WorkflowNode),
        ("edges", data.edges, WorkflowEdge),
    )
    for field_name, items, model_cls in collection_specs:
        if items is None:
            continue
        updates[field_name] = _validate_collection(
            items,
            model_cls,
            field_name=field_name,
        )
    return updates


def _nodes_from_blueprint(
    bp: BlueprintData,
) -> tuple[WorkflowNode, ...]:
    """Convert blueprint nodes to workflow nodes.

    Returns:
        Tuple of the declared element types.
    """
    return tuple(
        WorkflowNode(
            id=n.id,
            type=n.type,
            label=n.label,
            position_x=n.position_x,
            position_y=n.position_y,
            config=copy.deepcopy(n.config),
        )
        for n in bp.nodes
    )


def _edges_from_blueprint(
    bp: BlueprintData,
) -> tuple[WorkflowEdge, ...]:
    """Convert blueprint edges to workflow edges.

    Returns:
        Tuple of the declared element types.
    """
    return tuple(
        WorkflowEdge(
            id=e.id,
            source_node_id=e.source_node_id,
            target_node_id=e.target_node_id,
            type=e.type,
            label=e.label,
        )
        for e in bp.edges
    )


def build_definition_from_blueprint(
    bp: BlueprintData,
    data: CreateFromBlueprintRequest,
    creator: str,
    now: datetime,
) -> WorkflowDefinition:
    """Build a ``WorkflowDefinition`` from a loaded blueprint.

    Returns:
        ``WorkflowDefinition`` instance.
    """
    return WorkflowDefinition(
        id=f"wfdef-{uuid.uuid4().hex[:12]}",
        name=data.name or bp.display_name,
        description=(
            data.description if data.description is not None else bp.description
        ),
        workflow_type=bp.workflow_type,
        nodes=_nodes_from_blueprint(bp),
        edges=_edges_from_blueprint(bp),
        created_by=creator,
        created_at=now,
        updated_at=now,
    )


def apply_update(
    existing: WorkflowDefinition,
    data: UpdateWorkflowDefinitionRequest,
) -> WorkflowDefinition:
    """Merge update fields into an existing definition and validate.

    Raises:
        WorkflowDefinitionValidationError: 422 if the merged payload
            fails Pydantic validation. Pydantic detail is scrubbed so
            the envelope does not leak internal payload shapes; the
            structured warning log preserves operator context.

    Returns:
        ``WorkflowDefinition`` instance.
    """
    updates = build_update_fields(data)
    updates["revision"] = existing.revision + 1

    try:
        merged = existing.model_dump() | updates
        return WorkflowDefinition.model_validate(merged)
    except (ValueError, ValidationError) as exc:
        logger.warning(
            WORKFLOW_DEF_INVALID_REQUEST,
            definition_id=existing.id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = WorkflowDefinitionValidationError.default_message
        raise WorkflowDefinitionValidationError(msg) from exc


async def load_blueprint_or_raise(
    blueprint_name: str,
) -> BlueprintData:
    """Load a blueprint by name, raising the typed domain error on failure.

    Emits the per-attempt warning + metric so the audit stream records
    "blueprint resolution attempted -> outcome" pairs regardless of
    where the typed error is finally rendered.

    Raises:
        BlueprintNotFoundError: 404 + ``RESOURCE_NOT_FOUND``.
        BlueprintValidationError: 422 + ``VALIDATION_ERROR``.

    Returns:
        ``BlueprintData`` instance.
    """
    try:
        return await asyncio.to_thread(load_blueprint, blueprint_name)
    except BlueprintNotFoundError as exc:
        logger.warning(
            BLUEPRINT_INSTANTIATE_FAILED,
            blueprint_name=blueprint_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        record_blueprint_instantiation(
            outcome="not_found",
            blueprint_name=blueprint_name,
        )
        raise
    except BlueprintValidationError as exc:
        logger.warning(
            BLUEPRINT_INSTANTIATE_FAILED,
            blueprint_name=blueprint_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        record_blueprint_instantiation(
            outcome="validation_error",
            blueprint_name=blueprint_name,
        )
        raise
