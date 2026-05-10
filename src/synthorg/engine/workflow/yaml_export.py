"""Export a validated WorkflowDefinition as YAML.

Performs a topological sort of the graph and emits a flat list of
steps with dependency references that the engine's coordination/
decomposition subsystem can consume.
"""

from typing import TYPE_CHECKING, Any

import yaml

from synthorg.core.enums import WorkflowEdgeType, WorkflowNodeType
from synthorg.engine.workflow.graph_utils import (
    build_adjacency_maps,
    topological_sort,
)
from synthorg.engine.workflow.yaml_step_builders import STEP_BUILDERS
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_EXPORT_FAILED,
    WORKFLOW_DEF_EXPORTED,
)

if TYPE_CHECKING:
    from synthorg.engine.workflow.definition import WorkflowDefinition

logger = get_logger(__name__)


def _build_step(
    node_id: str,
    node_type: WorkflowNodeType,
    config: dict[str, Any],
    incoming_node_ids: list[str],
    outgoing_edges: list[tuple[str, WorkflowEdgeType]],
) -> dict[str, Any]:
    """Build a single step dict for the YAML output."""
    step: dict[str, Any] = {"id": node_id, "type": node_type.value}

    STEP_BUILDERS[node_type](step, config, outgoing_edges)

    depends_on = [nid for nid in incoming_node_ids if nid != node_id]
    if depends_on:
        step["depends_on"] = depends_on

    return step


def _assemble_document(
    definition: WorkflowDefinition,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the top-level YAML document structure."""
    body: dict[str, Any] = {
        "name": definition.name,
        "workflow_type": definition.workflow_type.value,
        "version": definition.version,
        "is_subworkflow": definition.is_subworkflow,
    }
    if definition.description:
        body["description"] = definition.description
    if definition.inputs:
        body["inputs"] = [i.model_dump(mode="json") for i in definition.inputs]
    if definition.outputs:
        body["outputs"] = [o.model_dump(mode="json") for o in definition.outputs]
    body["steps"] = steps
    return {"workflow_definition": body}


def _generate_steps(
    sorted_ids: list[str],
    node_map: dict[str, Any],
    reverse_adj: dict[str, list[str]],
    outgoing_edges: dict[str, list[tuple[str, WorkflowEdgeType]]],
) -> list[dict[str, Any]]:
    """Build step dicts from topologically sorted node IDs."""
    skip = {WorkflowNodeType.START, WorkflowNodeType.END}
    steps: list[dict[str, Any]] = []
    for node_id in sorted_ids:
        node = node_map[node_id]
        if node.type in skip:
            continue
        incoming = [
            src
            for src in reverse_adj.get(node_id, [])
            if node_map[src].type not in skip
        ]
        steps.append(
            _build_step(
                node_id=node_id,
                node_type=node.type,
                config=dict(node.config),
                incoming_node_ids=incoming,
                outgoing_edges=outgoing_edges.get(node_id, []),
            )
        )
    return steps


def _serialize_yaml(
    document: dict[str, Any],
    workflow_id: str,
) -> str:
    """Serialize document to YAML, wrapping errors as ValueError."""
    try:
        return yaml.dump(
            document,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
    except yaml.YAMLError as exc:
        msg = f"YAML serialization failed: {safe_error_description(exc)}"
        logger.warning(
            WORKFLOW_DEF_EXPORT_FAILED,
            workflow_id=workflow_id,
            reason="yaml_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise ValueError(msg) from exc


def export_workflow_yaml(definition: WorkflowDefinition) -> str:
    """Export a workflow definition as a YAML string.

    Args:
        definition: A validated workflow definition.

    Returns:
        YAML string representation of the workflow.

    Raises:
        ValueError: If the graph contains a cycle or YAML
            serialization fails.
    """
    node_map = {n.id: n for n in definition.nodes}
    adjacency, reverse_adj, outgoing_edges = build_adjacency_maps(definition)

    try:
        sorted_ids = topological_sort(
            [n.id for n in definition.nodes],
            adjacency,
        )
    except ValueError as exc:
        logger.warning(
            WORKFLOW_DEF_EXPORT_FAILED,
            workflow_id=definition.id,
            reason="cycle_detected",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise

    steps = _generate_steps(sorted_ids, node_map, reverse_adj, outgoing_edges)
    document = _assemble_document(definition, steps)
    result = _serialize_yaml(document, definition.id)

    logger.info(
        WORKFLOW_DEF_EXPORTED,
        workflow_id=definition.id,
        step_count=len(steps),
    )

    return result
