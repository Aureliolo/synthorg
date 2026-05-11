"""Edge-type constraint validation for workflow graphs.

Covers conditional edges (TRUE/FALSE), parallel split branches,
and verification edges (PASS/FAIL/REFER).
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.enums import WorkflowEdgeType, WorkflowNodeType
from synthorg.engine.workflow.validation_types import (
    ValidationErrorCode,
    WorkflowValidationError,
)

if TYPE_CHECKING:
    from synthorg.engine.workflow.definition import WorkflowDefinition

_MIN_SPLIT_BRANCHES: Final[int] = 2

_CONDITIONAL_EDGE_TYPES = frozenset(
    {
        WorkflowEdgeType.CONDITIONAL_TRUE,
        WorkflowEdgeType.CONDITIONAL_FALSE,
    }
)

_VERIFICATION_EDGE_TYPES = frozenset(
    {
        WorkflowEdgeType.VERIFICATION_PASS,
        WorkflowEdgeType.VERIFICATION_FAIL,
        WorkflowEdgeType.VERIFICATION_REFER,
    }
)


def check_conditional_edges(
    definition: WorkflowDefinition,
    outgoing: dict[str, list[WorkflowEdgeType]],
) -> list[WorkflowValidationError]:
    """Validate conditional node edge constraints."""
    errors: list[WorkflowValidationError] = []
    for node in definition.nodes:
        if node.type != WorkflowNodeType.CONDITIONAL:
            continue
        out_types = outgoing.get(node.id, [])
        if WorkflowEdgeType.CONDITIONAL_TRUE not in out_types:
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.CONDITIONAL_MISSING_TRUE,
                    message=(f"Conditional node {node.id!r} missing TRUE branch"),
                    node_id=node.id,
                )
            )
        if WorkflowEdgeType.CONDITIONAL_FALSE not in out_types:
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.CONDITIONAL_MISSING_FALSE,
                    message=(f"Conditional node {node.id!r} missing FALSE branch"),
                    node_id=node.id,
                )
            )
        extra = [t for t in out_types if t not in _CONDITIONAL_EDGE_TYPES]
        if extra:
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.CONDITIONAL_EXTRA_OUTGOING,
                    message=(
                        f"Conditional node {node.id!r} has "
                        f"non-conditional outgoing edges: {extra}"
                    ),
                    node_id=node.id,
                )
            )
    return errors


def check_parallel_splits(
    definition: WorkflowDefinition,
    outgoing: dict[str, list[WorkflowEdgeType]],
) -> list[WorkflowValidationError]:
    """Validate parallel split nodes have enough branches."""
    errors: list[WorkflowValidationError] = []
    for node in definition.nodes:
        if node.type != WorkflowNodeType.PARALLEL_SPLIT:
            continue
        out_types = outgoing.get(node.id, [])
        count = out_types.count(WorkflowEdgeType.PARALLEL_BRANCH)
        if count < _MIN_SPLIT_BRANCHES:
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.SPLIT_TOO_FEW_BRANCHES,
                    message=(
                        f"Parallel split {node.id!r} has {count} "
                        f"branch(es), needs at least "
                        f"{_MIN_SPLIT_BRANCHES}"
                    ),
                    node_id=node.id,
                )
            )
    return errors


def check_verification_edges(
    definition: WorkflowDefinition,
    outgoing: dict[str, list[WorkflowEdgeType]],
) -> list[WorkflowValidationError]:
    """Validate verification node edge constraints."""
    errors: list[WorkflowValidationError] = []
    for node in definition.nodes:
        if node.type != WorkflowNodeType.VERIFICATION:
            continue
        out_types = outgoing.get(node.id, [])
        if WorkflowEdgeType.VERIFICATION_PASS not in out_types:
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.VERIFICATION_MISSING_PASS,
                    message=f"Verification node {node.id!r} missing PASS edge",
                    node_id=node.id,
                )
            )
        if WorkflowEdgeType.VERIFICATION_FAIL not in out_types:
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.VERIFICATION_MISSING_FAIL,
                    message=f"Verification node {node.id!r} missing FAIL edge",
                    node_id=node.id,
                )
            )
        if WorkflowEdgeType.VERIFICATION_REFER not in out_types:
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.VERIFICATION_MISSING_REFER,
                    message=f"Verification node {node.id!r} missing REFER edge",
                    node_id=node.id,
                )
            )
        errors.extend(
            WorkflowValidationError(
                code=ValidationErrorCode.VERIFICATION_DUPLICATE_EDGE,
                message=(
                    f"Verification node {node.id!r} has duplicate "
                    f"{edge_type.value} edge"
                ),
                node_id=node.id,
            )
            for edge_type in _VERIFICATION_EDGE_TYPES
            if out_types.count(edge_type) > 1
        )
        extra = [t for t in out_types if t not in _VERIFICATION_EDGE_TYPES]
        if extra:
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.VERIFICATION_EXTRA_OUTGOING,
                    message=(
                        f"Verification node {node.id!r} has "
                        f"non-verification outgoing edges: {extra}"
                    ),
                    node_id=node.id,
                )
            )
    return errors


def check_verification_edge_scope(
    definition: WorkflowDefinition,
    outgoing: dict[str, list[WorkflowEdgeType]],
) -> list[WorkflowValidationError]:
    """Reject verification edges leaving non-verification nodes."""
    errors: list[WorkflowValidationError] = []
    for node in definition.nodes:
        if node.type == WorkflowNodeType.VERIFICATION:
            continue
        out_types = outgoing.get(node.id, [])
        bad = [t for t in out_types if t in _VERIFICATION_EDGE_TYPES]
        if bad:
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.VERIFICATION_EDGE_OUTSIDE,
                    message=(
                        f"Non-verification node {node.id!r} has "
                        f"verification edge(s): {bad}"
                    ),
                    node_id=node.id,
                )
            )
    return errors
