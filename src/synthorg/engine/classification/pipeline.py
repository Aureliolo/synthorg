"""Error classification pipeline.

Orchestrates the detection of coordination errors from an execution
result using the configured error taxonomy.  The pipeline never raises
exceptions — all errors are caught and logged.
"""

from typing import TYPE_CHECKING

from synthorg.budget.coordination_config import (
    ErrorCategory,
    ErrorTaxonomyConfig,
)
from synthorg.engine.classification.detectors import (
    detect_context_omissions,
    detect_coordination_failures,
    detect_logical_contradictions,
    detect_numerical_drift,
)
from synthorg.engine.classification.models import (
    ClassificationResult,
    ErrorFinding,
)
from synthorg.observability import get_logger
from synthorg.observability.events.classification import (
    CLASSIFICATION_COMPLETE,
    CLASSIFICATION_ERROR,
    CLASSIFICATION_FINDING,
    CLASSIFICATION_SKIPPED,
    CLASSIFICATION_START,
    DETECTOR_ERROR,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.core.types import NotBlankStr
    from synthorg.engine.loop_protocol import ExecutionResult

logger = get_logger(__name__)


async def classify_execution_errors(
    execution_result: ExecutionResult,
    agent_id: NotBlankStr,
    task_id: NotBlankStr,
    *,
    config: ErrorTaxonomyConfig,
) -> ClassificationResult | None:
    """Classify coordination errors from an execution result.

    Returns ``None`` when the taxonomy is disabled.  Never raises —
    all exceptions are caught and logged as ``CLASSIFICATION_ERROR``.

    The function is async for compatibility with the engine's async
    execution pipeline; current detectors run synchronously.

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

    execution_id = execution_result.context.execution_id
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
    """Run enabled detectors and collect findings.

    Each detector is invoked in its own try/except so that a failure
    in one detector does not prevent the others from running.
    """
    conversation = execution_result.context.conversation
    turns = execution_result.turns
    categories = config.categories
    msg_count = len(conversation)

    all_findings: list[ErrorFinding] = []

    if ErrorCategory.LOGICAL_CONTRADICTION in categories:
        all_findings.extend(
            _safe_detect(
                lambda: detect_logical_contradictions(conversation),
                "logical_contradictions",
                agent_id,
                task_id,
                execution_id,
                message_count=msg_count,
            ),
        )

    if ErrorCategory.NUMERICAL_DRIFT in categories:
        all_findings.extend(
            _safe_detect(
                lambda: detect_numerical_drift(conversation),
                "numerical_drift",
                agent_id,
                task_id,
                execution_id,
                message_count=msg_count,
            ),
        )

    if ErrorCategory.CONTEXT_OMISSION in categories:
        all_findings.extend(
            _safe_detect(
                lambda: detect_context_omissions(conversation),
                "context_omissions",
                agent_id,
                task_id,
                execution_id,
                message_count=msg_count,
            ),
        )

    if ErrorCategory.COORDINATION_FAILURE in categories:
        all_findings.extend(
            _safe_detect(
                lambda: detect_coordination_failures(
                    conversation,
                    turns,
                ),
                "coordination_failures",
                agent_id,
                task_id,
                execution_id,
                message_count=msg_count,
            ),
        )

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


def _safe_detect(  # noqa: PLR0913
    detector_fn: Callable[[], tuple[ErrorFinding, ...]],
    detector_name: str,
    agent_id: str,
    task_id: str,
    execution_id: str,
    *,
    message_count: int,
) -> tuple[ErrorFinding, ...]:
    """Run a single detector with isolation.

    Re-raises ``MemoryError`` and ``RecursionError``; catches and
    logs all other exceptions without stopping the pipeline.

    Args:
        detector_fn: Zero-arg callable that returns findings.
        detector_name: Name for logging.
        agent_id: Agent identifier.
        task_id: Task identifier.
        execution_id: Execution run identifier.
        message_count: Number of messages in the conversation
            (included in error logs for debuggability).

    Returns:
        Detector findings, or empty tuple on failure.
    """
    try:
        return detector_fn()
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.exception(
            DETECTOR_ERROR,
            agent_id=agent_id,
            task_id=task_id,
            execution_id=execution_id,
            detector=detector_name,
            message_count=message_count,
        )
        return ()
