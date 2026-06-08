"""Config completeness checks for workflow nodes."""

from typing import TYPE_CHECKING

from synthorg.engine.workflow.enums import WorkflowNodeType
from synthorg.engine.workflow.validation_types import (
    ValidationErrorCode,
    WorkflowValidationError,
)

if TYPE_CHECKING:
    from synthorg.engine.workflow.definition import WorkflowDefinition


def check_task_configs(
    definition: WorkflowDefinition,
) -> list[WorkflowValidationError]:
    """Validate task nodes have required config fields.

    Returns:
        List of validation errors for TASK nodes missing a non-blank
        ``title`` field.
    """
    errors: list[WorkflowValidationError] = []
    for node in definition.nodes:
        if node.type != WorkflowNodeType.TASK:
            continue
        title = node.config.get("title")
        if not title or (isinstance(title, str) and not title.strip()):
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.TASK_MISSING_TITLE,
                    message=(f"Task node {node.id!r} missing title in config"),
                    node_id=node.id,
                )
            )
    return errors


def check_verification_configs(
    definition: WorkflowDefinition,
) -> list[WorkflowValidationError]:
    """Validate verification nodes have required config fields.

    Returns:
        List of validation errors for VERIFICATION nodes missing
        ``rubric_name`` or ``evaluator_agent_id`` config fields.
    """
    errors: list[WorkflowValidationError] = []
    for node in definition.nodes:
        if node.type != WorkflowNodeType.VERIFICATION:
            continue
        rubric_name = node.config.get("rubric_name")
        if not isinstance(rubric_name, str) or not rubric_name.strip():
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.VERIFICATION_MISSING_CONFIG,
                    message=(
                        f"Verification node {node.id!r} missing rubric_name in config"
                    ),
                    node_id=node.id,
                )
            )
        evaluator = node.config.get("evaluator_agent_id")
        if not isinstance(evaluator, str) or not evaluator.strip():
            errors.append(
                WorkflowValidationError(
                    code=ValidationErrorCode.VERIFICATION_MISSING_CONFIG,
                    message=(
                        f"Verification node {node.id!r} missing "
                        f"evaluator_agent_id in config"
                    ),
                    node_id=node.id,
                )
            )
    return errors
