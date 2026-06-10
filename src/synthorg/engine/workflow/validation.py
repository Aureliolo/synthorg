"""Graph-level validation for workflow definitions.

Top-level entry points that orchestrate validation checks
across reachability, edges, configs, and subworkflows.
"""

from collections import defaultdict

from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.engine.workflow.enums import WorkflowEdgeType
from synthorg.engine.workflow.validate_configs import (
    check_task_configs,
    check_verification_configs,
)
from synthorg.engine.workflow.validate_edges import (
    check_conditional_edges,
    check_parallel_splits,
    check_verification_edge_scope,
    check_verification_edges,
)
from synthorg.engine.workflow.validate_reachability import (
    check_reachability,
    has_cycle,
)
from synthorg.engine.workflow.validate_subworkflow import (
    validate_subworkflow_graph,
    validate_subworkflow_io,
)
from synthorg.engine.workflow.validation_types import (
    ValidationErrorCode,
    WorkflowValidationError,
    WorkflowValidationResult,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_VALIDATED,
    WORKFLOW_DEF_VALIDATION_FAILED,
)

logger = get_logger(__name__)

__all__ = [
    "ValidationErrorCode",
    "WorkflowValidationError",
    "WorkflowValidationResult",
    "validate_subworkflow_graph",
    "validate_subworkflow_io",
    "validate_workflow",
]


def validate_workflow(
    definition: WorkflowDefinition,
) -> WorkflowValidationResult:
    """Validate a workflow definition for execution readiness.

    Args:
        definition: The workflow definition to validate.

    Returns:
        Validation result with any errors found.
    """
    adjacency: dict[str, list[str]] = defaultdict(list)
    outgoing_types: dict[str, list[WorkflowEdgeType]] = defaultdict(list)
    for edge in definition.edges:
        adjacency[edge.source_node_id].append(edge.target_node_id)
        outgoing_types[edge.source_node_id].append(edge.type)

    errors: list[WorkflowValidationError] = []
    errors.extend(check_reachability(definition, adjacency))
    errors.extend(check_conditional_edges(definition, outgoing_types))
    errors.extend(check_parallel_splits(definition, outgoing_types))
    errors.extend(check_task_configs(definition))
    errors.extend(check_verification_edges(definition, outgoing_types))
    errors.extend(check_verification_edge_scope(definition, outgoing_types))
    errors.extend(check_verification_configs(definition))

    all_ids = frozenset(n.id for n in definition.nodes)
    if has_cycle(all_ids, adjacency):
        errors.append(
            WorkflowValidationError(
                code=ValidationErrorCode.CYCLE_DETECTED,
                message="Workflow graph contains a cycle",
            )
        )

    result = WorkflowValidationResult(errors=tuple(errors))

    if result.valid:
        logger.info(WORKFLOW_DEF_VALIDATED, workflow_id=str(definition.id))
    else:
        logger.warning(
            WORKFLOW_DEF_VALIDATION_FAILED,
            workflow_id=str(definition.id),
            error_count=len(errors),
        )

    return result
