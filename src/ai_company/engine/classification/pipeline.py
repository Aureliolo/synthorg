"""Error classification pipeline.

Orchestrates the detection of coordination errors from an execution
result using the configured error taxonomy.  The pipeline never raises
exceptions — all errors are caught and logged.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from ai_company.budget.coordination_config import (
    ErrorCategory,
    ErrorTaxonomyConfig,
)
from ai_company.engine.classification.detectors import (
    detect_context_omissions,
    detect_coordination_failures,
    detect_logical_contradictions,
    detect_numerical_drift,
)
from ai_company.engine.classification.models import (
    ClassificationResult,
    ErrorFinding,
)
from ai_company.observability import get_logger

if TYPE_CHECKING:
    from ai_company.engine.loop_protocol import ExecutionResult
from ai_company.observability.events.classification import (
    CLASSIFICATION_COMPLETE,
    CLASSIFICATION_ERROR,
    CLASSIFICATION_FINDING,
    CLASSIFICATION_SKIPPED,
    CLASSIFICATION_START,
)

logger = get_logger(__name__)


async def classify_execution_errors(
    execution_result: ExecutionResult,
    agent_id: str,
    task_id: str,
    *,
    config: ErrorTaxonomyConfig,
) -> ClassificationResult | None:
    """Classify coordination errors from an execution result.

    Returns ``None`` when the taxonomy is disabled.  Never raises —
    all exceptions are caught and logged as ``CLASSIFICATION_ERROR``.

    Args:
        execution_result: The completed execution result to analyse.
        agent_id: Agent that executed the task.
        task_id: Task that was executed.
        config: Error taxonomy configuration controlling which
            categories to check.

    Returns:
        Classification result with findings, or ``None`` if disabled.
    """
    if not config.enabled:
        logger.debug(
            CLASSIFICATION_SKIPPED,
            agent_id=agent_id,
            task_id=task_id,
            reason="error taxonomy disabled",
        )
        return None

    execution_id = str(uuid4())
    logger.info(
        CLASSIFICATION_START,
        agent_id=agent_id,
        task_id=task_id,
        execution_id=execution_id,
        categories=tuple(c.value for c in config.categories),
    )

    try:
        return _run_detectors(
            execution_result,
            agent_id,
            task_id,
            execution_id=execution_id,
            config=config,
        )
    except MemoryError, RecursionError:
        logger.error(
            CLASSIFICATION_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            error="non-recoverable error in classification",
            exc_info=True,
        )
        raise
    except Exception as exc:
        logger.exception(
            CLASSIFICATION_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None


def _run_detectors(
    execution_result: ExecutionResult,
    agent_id: str,
    task_id: str,
    *,
    execution_id: str,
    config: ErrorTaxonomyConfig,
) -> ClassificationResult:
    """Run enabled detectors and collect findings."""
    conversation = execution_result.context.conversation
    turns = execution_result.turns
    categories = config.categories

    all_findings: list[ErrorFinding] = []

    if ErrorCategory.LOGICAL_CONTRADICTION in categories:
        all_findings.extend(detect_logical_contradictions(conversation))

    if ErrorCategory.NUMERICAL_DRIFT in categories:
        all_findings.extend(detect_numerical_drift(conversation))

    if ErrorCategory.CONTEXT_OMISSION in categories:
        all_findings.extend(detect_context_omissions(conversation))

    if ErrorCategory.COORDINATION_FAILURE in categories:
        all_findings.extend(detect_coordination_failures(conversation, turns))

    for finding in all_findings:
        logger.info(
            CLASSIFICATION_FINDING,
            agent_id=agent_id,
            task_id=task_id,
            execution_id=execution_id,
            category=finding.category.value,
            severity=finding.severity.value,
            description=finding.description,
        )

    result = ClassificationResult(
        execution_id=execution_id,
        agent_id=agent_id,
        task_id=task_id,
        categories_checked=categories,
        findings=tuple(all_findings),
    )

    logger.info(
        CLASSIFICATION_COMPLETE,
        agent_id=agent_id,
        task_id=task_id,
        execution_id=execution_id,
        finding_count=result.finding_count,
    )

    return result
